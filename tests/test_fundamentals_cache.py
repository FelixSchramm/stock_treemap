"""Cache codec and protected store with invented statements; no provider."""

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from test_fundamentals_loader import make_subject
from test_fundamentals_normalization import RETRIEVED, statements

from portfolio_dashboard.config import (
    AppConfig,
    CacheTTLs,
    LoginConfig,
    Mode,
    Provider,
    SourceConfig,
)
from portfolio_dashboard.fundamentals_cache import (
    CODEC_VERSION,
    CacheRecord,
    FailedAttempt,
    MemoryFundamentalsStore,
    PrivateFundamentalsStore,
    StoredFetch,
    decode_record,
    encode_record,
    store_key,
)
from portfolio_dashboard.fundamentals_normalization import FetchFailure
from portfolio_dashboard.private_store import PrivateDatabase, PrivateStorageError

CHECKED = RETRIEVED + timedelta(hours=3)


def success(**overrides):
    return StoredFetch(statements(**overrides), "1.7.0", CHECKED)


def attempt(**overrides):
    values = {
        "at": CHECKED,
        "codes": ("annual_balance_timeout",),
        "consecutive": 2,
        "retry_after": CHECKED + timedelta(minutes=10),
    }
    values.update(overrides)
    return FailedAttempt(**values)


def private_store(tmp_path):
    database = PrivateDatabase(
        lambda: AppConfig(
            Mode.LOCAL,
            SourceConfig(),
            Provider.SYNTHETIC,
            CacheTTLs(),
            LoginConfig(),
            str(tmp_path / "private"),
        ),
        server_address=lambda: "127.0.0.1",
    )
    return PrivateFundamentalsStore(database), tmp_path / "private" / "private.sqlite3"


def test_record_roundtrip_is_exact_including_decimals_and_gaps():
    record = CacheRecord(success(), attempt())
    decoded = decode_record(encode_record(record))
    assert decoded == record
    assert (
        decoded.success.statements.annual_balance
        == record.success.statements.annual_balance
    )
    assert decoded.success.statements.dated_shares[0][1] == Decimal(10_000_000)
    # A missing cell stays missing; it never becomes zero on the way through.
    rows = decoded.success.statements.annual_balance.rows
    assert (
        rows["Minority Interest"][RETRIEVED.date().replace(year=2023, month=12, day=31)]
        is None
    )


def test_payload_is_versioned_json_without_python_types():
    payload = json.loads(encode_record(CacheRecord(success(), None)))
    assert payload["codec"] == CODEC_VERSION and payload["attempt"] is None
    assert "StatementTable" not in json.dumps(payload) and "Decimal" not in json.dumps(
        payload
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(codec=2),
        lambda d: d.update(extra=1),
        lambda d: d.pop("attempt"),
        lambda d: d["success"].update(package_version=""),
        lambda d: d["success"].update(checked_at="2025-03-15T10:00:00"),  # Naive.
        lambda d: d["success"]["statements"].update(shares=[["2025-03-01"]]),
        lambda d: d["success"]["statements"]["info"].update(unexpected=1),
        lambda d: d["success"]["statements"]["info"].update(currency=True),
        lambda d: d["success"]["statements"]["income"].update(x={"not-a-date": "1"}),
        lambda d: d["success"]["statements"]["income"].update(x={"2024-12-31": "NaN"}),
        lambda d: d["success"]["statements"]["income"].update(x={"2024-12-31": 1.5}),
        lambda d: d["success"]["statements"].update(balance=None),
    ],
)
def test_malformed_payloads_are_rejected_with_the_fixed_error(mutate):
    data = json.loads(encode_record(CacheRecord(success(), None)))
    mutate(data)
    with pytest.raises(PrivateStorageError) as error:
        decode_record(json.dumps(data))
    assert error.value.code == "data"
    assert "Minority" not in str(error.value)


@pytest.mark.parametrize(
    "payload",
    ["", "not json", "[]", '{"codec": 1, "codec": 1}', '{"codec": NaN}'],
)
def test_garbage_payloads_are_rejected(payload):
    with pytest.raises(PrivateStorageError) as error:
        decode_record(payload)
    assert error.value.code == "data"


def test_only_complete_retrievals_are_storable():
    base = statements()
    for broken in (
        replace(base, annual_income=None),
        replace(base, info=None),
        replace(base, dated_shares=None),
        replace(base, failures=(FetchFailure("info", "timeout"),)),
    ):
        with pytest.raises(ValueError):
            StoredFetch(broken, "1.7.0", CHECKED)
    with pytest.raises(ValueError):
        StoredFetch(base, " ", CHECKED)
    with pytest.raises(ValueError):
        StoredFetch(base, "1.7.0", datetime(2025, 3, 15))


def test_failure_codes_are_sanitized_identifiers_never_free_text():
    for codes in (
        (),
        ("Timeout while contacting https://example.invalid",),
        ("SYM.X",),
        ("ok", ""),
    ):
        with pytest.raises(ValueError):
            attempt(codes=codes)
    with pytest.raises(ValueError):
        attempt(consecutive=0)
    with pytest.raises(ValueError):
        attempt(retry_after=datetime(2025, 3, 15))


def test_store_key_is_opaque_and_stable_per_security():
    one, two = make_subject("a"), make_subject("b")
    key = store_key(one.fetch_key)
    assert key == store_key(make_subject("a").fetch_key) != store_key(two.fetch_key)
    assert len(key) == 64 and "synthetic" not in key and "SYN" not in key


def test_records_have_no_repr_leak():
    text = repr(CacheRecord(success(), attempt()))
    assert (
        "Decimal" not in text
        and "150000000" not in text
        and "annual_balance" not in text
    )


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_failure_keeps_success_and_success_clears_failure(tmp_path, kind):
    store = (
        MemoryFundamentalsStore() if kind == "memory" else private_store(tmp_path)[0]
    )
    key = store_key(make_subject().fetch_key)
    assert store.get(key) is None
    store.save_failure(key, attempt(consecutive=1))
    assert store.get(key) == CacheRecord(None, attempt(consecutive=1))
    first = success()
    store.save_success(key, first)
    assert store.get(key) == CacheRecord(first, None)
    store.save_failure(key, attempt())
    assert store.get(key) == CacheRecord(first, attempt())  # Success untouched.
    second = StoredFetch(statements(), "1.8.0", CHECKED + timedelta(days=1))
    store.save_success(key, second)
    assert store.get(key) == CacheRecord(second, None)


def test_sqlite_rows_are_per_security_and_survive_a_new_handle(tmp_path):
    store, path = private_store(tmp_path)
    keys = [store_key(make_subject(name).fetch_key) for name in "ab"]
    store.save_success(keys[0], success())
    store.save_failure(keys[1], attempt())
    reopened, _ = private_store(tmp_path)
    assert reopened.get(keys[0]).success == success()
    assert reopened.get(keys[1]).success is None
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM fundamentals_cache"
        ).fetchone() == (2,)
        stored = [
            r[0] for r in connection.execute("SELECT fetch_key FROM fundamentals_cache")
        ]
    assert sorted(stored) == sorted(keys)
    assert path.stat().st_mode & 0o077 == 0


def test_a_tampered_row_fails_closed_without_a_fallback(tmp_path):
    store, path = private_store(tmp_path)
    key = store_key(make_subject().fetch_key)
    store.save_success(key, success())
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE fundamentals_cache SET payload = ?", ('{"codec": 1}',)
        )
    with pytest.raises(PrivateStorageError) as error:
        store.get(key)
    assert error.value.code == "data"
    with pytest.raises(PrivateStorageError):
        store.save_failure(key, attempt())  # No silent overwrite of a bad row.


def test_store_access_follows_the_private_database_rules(tmp_path):
    demo = PrivateFundamentalsStore(
        PrivateDatabase(
            lambda: AppConfig(
                Mode.DEMO,
                SourceConfig(),
                Provider.SYNTHETIC,
                CacheTTLs(),
                LoginConfig(),
                str(tmp_path / "x"),
            )
        )
    )
    with pytest.raises(PrivateStorageError) as error:
        demo.require_access()
    assert error.value.code == "access"
    assert not (tmp_path / "x").exists()
    store, _ = private_store(tmp_path)
    store.require_access()  # Local mode on loopback.
    remote = PrivateFundamentalsStore(
        PrivateDatabase(
            lambda: AppConfig(
                Mode.LOCAL,
                SourceConfig(),
                Provider.SYNTHETIC,
                CacheTTLs(),
                LoginConfig(),
                str(tmp_path / "y"),
            ),
            server_address=lambda: "0.0.0.0",
        )
    )
    with pytest.raises(PrivateStorageError) as error:
        remote.require_access()
    assert error.value.code == "local"


def test_moments_keep_their_time_zone():
    record = decode_record(encode_record(CacheRecord(success(), attempt())))
    assert record.success.checked_at.utcoffset() == timedelta(0)
    assert record.success.statements.retrieved_at.tzinfo is not None
    assert record.attempt.at.astimezone(UTC) == CHECKED
