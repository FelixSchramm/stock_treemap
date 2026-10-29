"""Protected persistence of raw fundamentals retrievals (M8-05, #74).

The cache holds the provider's raw ``ProviderStatements`` per verified security,
never normalized results: normalization is deterministic and is repeated on every
load, so a changed rule can never leave a stale interpretation behind. A record
keeps the last successful retrieval and, separately, the last failed attempt.
The two are never merged: a failed or partial retrieval does not touch the stored
statements, and statements of different retrievals are never combined.

Storage reuses the private SQLite foundation (``private_store``): the owner or
loopback guard runs before every transaction, demo data lives in a throwaway
database, and nothing is logged. The format is versioned JSON with exact shapes
and Decimal strings; a payload that does not match is ``PrivateStorageError("data")``.
Retrieval times are the evidence for the *age of the last retrieval*; they say
nothing about how old the business reports are (``issues_at`` judges that).
"""

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from numbers import Integral, Real
from typing import Protocol

from portfolio_dashboard.fundamentals_normalization import (
    INFO_KEYS,
    ProviderStatements,
    StatementTable,
)
from portfolio_dashboard.private_store import PrivateDatabase, PrivateStorageError

CODEC_VERSION = 1
CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
_TABLES = ("annual_income", "annual_balance", "annual_cashflow")


@dataclass(frozen=True, slots=True, repr=False)
class StoredFetch:
    """The last successful, complete retrieval.

    ``statements.retrieved_at`` is when this exact content was first delivered
    (it names the report set); ``checked_at`` is the latest successful retrieval
    that delivered the same content or this content for the first time.
    """

    statements: ProviderStatements
    package_version: str
    checked_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.statements, ProviderStatements):
            raise TypeError("statements must be ProviderStatements")
        if self.statements.failures or any(
            getattr(self.statements, name) is None
            for name in (*_TABLES, "info", "dated_shares")
        ):
            raise ValueError("only complete retrievals are stored")
        if (
            not isinstance(self.package_version, str)
            or not self.package_version.strip()
        ):
            raise ValueError("package_version must be nonblank text")
        _aware(self.checked_at)


@dataclass(frozen=True, slots=True, repr=False)
class FailedAttempt:
    """The last failed retrieval: sanitized codes only, with its backoff state."""

    at: datetime
    codes: tuple[str, ...]
    consecutive: int
    retry_after: datetime | None = None

    def __post_init__(self) -> None:
        _aware(self.at)
        if self.retry_after is not None:
            _aware(self.retry_after)
        if (
            not isinstance(self.codes, tuple)
            or not self.codes
            or not all(
                isinstance(c, str) and CODE_PATTERN.fullmatch(c) for c in self.codes
            )
        ):
            raise ValueError("failure codes must be sanitized identifiers")
        if type(self.consecutive) is not int or self.consecutive < 1:
            raise ValueError("consecutive must be a positive integer")


@dataclass(frozen=True, slots=True, repr=False)
class CacheRecord:
    success: StoredFetch | None = None
    attempt: FailedAttempt | None = None

    def __post_init__(self) -> None:
        if self.success is not None and not isinstance(self.success, StoredFetch):
            raise TypeError("success must be StoredFetch")
        if self.attempt is not None and not isinstance(self.attempt, FailedAttempt):
            raise TypeError("attempt must be FailedAttempt")


class FundamentalsStore(Protocol):
    """What the loader needs; the private SQLite store and the demo store fit."""

    def require_access(self) -> None: ...

    def get(self, key: str) -> CacheRecord | None: ...

    def save_success(self, key: str, success: StoredFetch) -> None: ...

    def save_failure(self, key: str, attempt: FailedAttempt) -> None: ...


def _aware(value: object) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timezone-aware datetime required")


def store_key(fetch_key: tuple) -> str:
    """Opaque row key over the verified identity; no symbol or id is a column."""
    instrument_id, share_class_id, issuer_id, source, scope = fetch_key
    parts = [
        instrument_id,
        share_class_id,
        issuer_id,
        source.provider,
        source.entity_id,
        scope,
    ]
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


# --- codec --------------------------------------------------------------------


def _object(value: object, keys: str) -> dict:
    if type(value) is not dict or set(value) != set(keys.split()):
        raise ValueError("invalid stored structure")
    return value


def _moment(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid stored time")
    moment = datetime.fromisoformat(value)
    _aware(moment)
    return moment


def _day(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("invalid stored date")
    return date.fromisoformat(value)


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("invalid stored number")
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("invalid stored number")
    return number


def _encode_table(table: StatementTable) -> dict:
    return {
        label: {
            end.isoformat(): None if value is None else str(value)
            for end, value in cells.items()
        }
        for label, cells in table.rows.items()
    }


def _decode_table(value: object) -> StatementTable:
    if type(value) is not dict:
        raise ValueError("invalid stored table")
    return StatementTable(
        {
            label: {
                _day(end): None if cell is None else _decimal(cell)
                for end, cell in _dict(cells).items()
            }
            for label, cells in value.items()
        }
    )


def _dict(value: object) -> dict:
    if type(value) is not dict:
        raise ValueError("invalid stored structure")
    return value


def _encode_info(info: Mapping) -> dict:
    if set(info) != set(INFO_KEYS):
        raise ValueError("unexpected metadata keys")
    encoded = {}
    for key, value in info.items():
        if value is None or isinstance(value, str):
            encoded[key] = value
        elif isinstance(value, bool):
            raise ValueError("unsupported metadata value")
        elif isinstance(value, Integral):
            encoded[key] = int(value)
        elif isinstance(value, Real) and math.isfinite(float(value)):
            encoded[key] = float(value)
        else:
            raise ValueError("unsupported metadata value")
    return encoded


def _decode_info(value: object) -> dict:
    info = _object(value, " ".join(INFO_KEYS))
    for item in info.values():
        if isinstance(item, bool) or not (
            item is None or isinstance(item, (str, int, float))
        ):
            raise ValueError("unsupported metadata value")
    return dict(info)


def encode_record(record: CacheRecord) -> str:
    success = record.success
    attempt = record.attempt
    payload = {
        "codec": CODEC_VERSION,
        "success": None
        if success is None
        else {
            "package_version": success.package_version,
            "checked_at": success.checked_at.isoformat(),
            "statements": {
                "retrieved_at": success.statements.retrieved_at.isoformat(),
                "income": _encode_table(success.statements.annual_income),
                "balance": _encode_table(success.statements.annual_balance),
                "cashflow": _encode_table(success.statements.annual_cashflow),
                "info": _encode_info(success.statements.info),
                "shares": [
                    [day.isoformat(), str(count)]
                    for day, count in success.statements.dated_shares
                ],
            },
        },
        "attempt": None
        if attempt is None
        else {
            "at": attempt.at.isoformat(),
            "codes": list(attempt.codes),
            "consecutive": attempt.consecutive,
            "retry_after": None
            if attempt.retry_after is None
            else attempt.retry_after.isoformat(),
        },
    }
    return json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True)


def _reject_constant(_: str):
    raise ValueError("non-finite JSON constant")


def _pairs(pairs):
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def decode_record(payload: str) -> CacheRecord:
    """Exact shapes only; every contract validates itself again on construction."""
    try:
        data = _object(
            json.loads(
                payload, object_pairs_hook=_pairs, parse_constant=_reject_constant
            ),
            "codec success attempt",
        )
        if data["codec"] != CODEC_VERSION or type(data["codec"]) is not int:
            raise ValueError("unknown codec")
        success = None
        if data["success"] is not None:
            raw = _object(data["success"], "package_version checked_at statements")
            body = _object(
                raw["statements"],
                "retrieved_at income balance cashflow info shares",
            )
            shares = body["shares"]
            if type(shares) is not list:
                raise ValueError("invalid stored shares")
            success = StoredFetch(
                ProviderStatements(
                    _moment(body["retrieved_at"]),
                    annual_income=_decode_table(body["income"]),
                    annual_balance=_decode_table(body["balance"]),
                    annual_cashflow=_decode_table(body["cashflow"]),
                    info=_decode_info(body["info"]),
                    dated_shares=tuple(
                        (_day(item[0]), _decimal(item[1]))
                        for item in (_pair(entry) for entry in shares)
                    ),
                ),
                raw["package_version"],
                _moment(raw["checked_at"]),
            )
        attempt = None
        if data["attempt"] is not None:
            raw = _object(data["attempt"], "at codes consecutive retry_after")
            if type(raw["codes"]) is not list or type(raw["consecutive"]) is not int:
                raise ValueError("invalid stored attempt")
            attempt = FailedAttempt(
                _moment(raw["at"]),
                tuple(raw["codes"]),
                raw["consecutive"],
                None if raw["retry_after"] is None else _moment(raw["retry_after"]),
            )
        return CacheRecord(success, attempt)
    except (ValueError, TypeError, KeyError, InvalidOperation, RecursionError):
        raise PrivateStorageError("data") from None


def _pair(entry: object) -> list:
    if type(entry) is not list or len(entry) != 2:
        raise ValueError("invalid stored pair")
    return entry


# --- SQLite store ---------------------------------------------------------------


class PrivateFundamentalsStore:
    """Rows in the shared private database; every call passes the guard anew."""

    def __init__(self, database: PrivateDatabase):
        self._database = database

    def require_access(self) -> None:
        """Owner (production) or loopback (local); demo and others are refused."""
        self._database.require_real_access()

    def get(self, key: str) -> CacheRecord | None:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT payload FROM fundamentals_cache WHERE fetch_key = ?", (key,)
            ).fetchone()
        return None if row is None else decode_record(row[0])

    def save_success(self, key: str, success: StoredFetch) -> None:
        # A success replaces the record: the failed attempt it recovered from ends.
        payload = encode_record(CacheRecord(success, None))
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO fundamentals_cache (fetch_key, payload) "
                "VALUES (?, ?)",
                (key, payload),
            )

    def save_failure(self, key: str, attempt: FailedAttempt) -> None:
        """Keep the stored success untouched; read and write in one transaction."""
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT payload FROM fundamentals_cache WHERE fetch_key = ?", (key,)
            ).fetchone()
            kept = None if row is None else decode_record(row[0]).success
            connection.execute(
                "INSERT OR REPLACE INTO fundamentals_cache (fetch_key, payload) "
                "VALUES (?, ?)",
                (key, encode_record(CacheRecord(kept, attempt))),
            )


class MemoryFundamentalsStore:
    """Process-local store for the synthetic demo; it never touches the disk."""

    def __init__(self) -> None:
        self._records: dict[str, CacheRecord] = {}

    def require_access(self) -> None:
        return None

    def get(self, key: str) -> CacheRecord | None:
        return self._records.get(key)

    def save_success(self, key: str, success: StoredFetch) -> None:
        self._records[key] = CacheRecord(success, None)

    def save_failure(self, key: str, attempt: FailedAttempt) -> None:
        kept = self._records.get(key, CacheRecord()).success
        self._records[key] = CacheRecord(kept, attempt)
