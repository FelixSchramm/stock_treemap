"""Invented watchlist records only; no provider, document, Sheet or credentials."""

import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier

import pytest

from portfolio_dashboard.config import (
    AppConfig,
    CacheTTLs,
    LoginConfig,
    Mode,
    Provider,
    SourceConfig,
)
from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.models import Instrument, InstrumentType, Position, QuoteUnit
from portfolio_dashboard.private_store import PrivateDatabase, PrivateStorageError
from portfolio_dashboard.watchlist import (
    MappingStatus,
    WatchlistConflict,
    WatchlistEntry,
    WatchlistRepository,
    link_portfolio,
)


def listing(instrument_id="synthetic-share-a", venue="synthetic-venue"):
    return VerifiedListing(
        Instrument(
            InstrumentType.STOCK, instrument_id=instrument_id, trading_venue=venue
        ),
        equity_form=EquityForm.SHARE,
        isin="synthetic-isin-" + instrument_id,
    )


def local_config(directory):
    return AppConfig(
        Mode.LOCAL,
        SourceConfig(),
        Provider.SYNTHETIC,
        CacheTTLs(),
        LoginConfig(),
        str(directory),
    )


def repository(directory):
    return WatchlistRepository(
        PrivateDatabase(
            lambda: local_config(directory), server_address=lambda: "127.0.0.1"
        )
    )


def entry(key="synthetic-entry", checked=True):
    return WatchlistEntry(
        key, "Synthetische Beobachtung", listing() if checked else None
    )


def test_add_reload_update_remove_without_quantity_or_cost(tmp_path):
    directory = tmp_path / "private"
    first = repository(directory)
    original = entry()
    assert first.list() == ()
    assert first.add(original) is original
    assert first.get(original.entry_id) == original
    assert not hasattr(original, "quantity") and not hasattr(original, "cost_basis_eur")
    second = repository(directory)
    assert second.list() == (original,)
    updated = second.update(replace(original, label="Synthetische Änderung"))
    assert updated.revision == 2
    assert first.get(original.entry_id) == updated
    first.remove(updated.entry_id, expected_revision=updated.revision)
    assert second.list() == ()
    assert second.get(updated.entry_id) is None


def test_process_restart_reads_committed_watchlist(tmp_path):
    directory = tmp_path / "private"
    repository(directory).add(entry())
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from portfolio_dashboard.config import AppConfig, CacheTTLs, LoginConfig, Mode, Provider, SourceConfig
from portfolio_dashboard.private_store import PrivateDatabase
from portfolio_dashboard.watchlist import WatchlistRepository
config = AppConfig(Mode.LOCAL, SourceConfig(), Provider.SYNTHETIC, CacheTTLs(), LoginConfig(), sys.argv[1])
repo = WatchlistRepository(PrivateDatabase(lambda: config, server_address=lambda: '127.0.0.1'))
assert len(repo.list()) == 1
print('restart-ok')
""",
            str(directory),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "restart-ok"
    assert "Synthetische Beobachtung" not in result.stdout + result.stderr


def test_duplicate_confirmed_security_across_venues_is_rejected_but_classes_are_separate(
    tmp_path,
):
    repo = repository(tmp_path / "private")
    repo.add(entry())
    alternate = WatchlistEntry(
        "synthetic-alt", "Anderes Listing", listing(venue="synthetic-second-venue")
    )
    with pytest.raises(WatchlistConflict):
        repo.add(alternate)
    another_class = WatchlistEntry(
        "synthetic-class-b", "Andere Klasse", listing("synthetic-share-b")
    )
    repo.add(another_class)
    assert len(repo.list()) == 2
    with pytest.raises(WatchlistConflict):
        repo.add(entry())
    assert len(repo.list()) == 2


def test_unverified_duplicates_stay_visible_without_any_provider_input(tmp_path):
    repo = repository(tmp_path / "private")
    first, second = entry("synthetic-first", False), entry("synthetic-second", False)
    repo.add(first)
    repo.add(second)
    for item in repository(tmp_path / "private").list():
        assert item.instrument is None and item.provider_mapping is None
        assert item.mapping_status is MappingStatus.UNVERIFIED
        assert (
            "ungeprüft" in item.mapping_explanation
            and "kein automatischer Providerabruf" in item.mapping_explanation
        )
    checked_no_provider = entry()
    assert checked_no_provider.mapping_status is MappingStatus.COVERAGE_OPEN
    assert checked_no_provider.provider_mapping is None
    assert "Providerzuordnung offen" in checked_no_provider.mapping_explanation


def test_confirmation_update_checks_duplicates_atomically(tmp_path):
    repo = repository(tmp_path / "private")
    repo.add(entry())
    pending = repo.add(entry("synthetic-pending", False))
    with pytest.raises(WatchlistConflict):
        repo.update(replace(pending, listing=listing()))
    assert repo.get(pending.entry_id) == pending
    repo.remove("synthetic-entry", expected_revision=1)
    confirmed = repo.update(replace(pending, listing=listing()))
    assert confirmed.listing == listing() and confirmed.revision == 2


def test_stale_sessions_cannot_overwrite_or_delete_newer_change(tmp_path):
    first, second = repository(tmp_path / "private"), repository(tmp_path / "private")
    first.add(entry())
    stale = second.get("synthetic-entry")
    changed = first.update(replace(stale, label="Neue synthetische Fassung"))
    with pytest.raises(WatchlistConflict):
        second.update(replace(stale, label="Alte synthetische Fassung"))
    with pytest.raises(WatchlistConflict):
        second.remove(stale.entry_id, expected_revision=stale.revision)
    assert second.get(stale.entry_id) == changed
    first.remove(changed.entry_id, expected_revision=changed.revision)
    with pytest.raises(WatchlistConflict):
        second.update(changed)


def test_two_independent_sessions_write_concurrently_without_losing_entries(tmp_path):
    directory = tmp_path / "private"
    start = Barrier(2)

    def write(session):
        repo = repository(directory)
        start.wait(timeout=5)
        for index in range(15):
            repo.add(entry(f"synthetic-{session}-{index}", False))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(write, name) for name in ("first", "second")]
        for result in results:
            result.result(timeout=15)
    assert len(repository(directory).list()) == 30


def test_concurrent_duplicate_confirmation_has_exactly_one_winner(tmp_path):
    directory = tmp_path / "private"
    start = Barrier(2)

    def write(key):
        repo = repository(directory)
        start.wait(timeout=5)
        try:
            repo.add(entry(key))
            return "saved"
        except WatchlistConflict:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ("synthetic-one", "synthetic-two")))
    assert sorted(results) == ["duplicate", "saved"]
    assert len(repository(directory).list()) == 1


def test_busy_database_never_reports_success(tmp_path):
    directory = tmp_path / "private"
    repo = repository(directory)
    repo.add(entry())
    with sqlite3.connect(directory / "private.sqlite3", isolation_level=None) as locked:
        locked.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(PrivateStorageError) as error:
                repo.add(entry("synthetic-waiting", False))
            assert error.value.code == "busy"
        finally:
            locked.rollback()
    assert len(repo.list()) == 1
    repo.add(entry("synthetic-waiting", False))
    assert len(repo.list()) == 2


def test_portfolio_overlap_shares_exact_security_key_without_touching_positions():
    a, alternate, b = (
        listing(),
        listing(venue="synthetic-other-venue"),
        listing("synthetic-share-b"),
    )
    entries = (
        entry(),
        WatchlistEntry("synthetic-b", "Synthetische Klasse B", b),
        entry("synthetic-pending", False),
    )
    # Position field order intentionally follows the existing model contract.
    positions = (
        Position(
            source_row=2,
            name="Synthetische Aktie",
            instrument=alternate.instrument,
            quantity=Decimal(7),
            category=None,
            imported_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    original = positions[0]
    links = link_portfolio(entries, positions, (a, alternate, b))
    assert [link.in_portfolio for link in links] == [True, False, False]
    assert [link.valuation_key for link in links] == [
        "synthetic-share-a",
        "synthetic-share-b",
        None,
    ]
    assert positions[0] is original and positions[0].quantity == Decimal(7)
    unverified = replace(
        original,
        instrument=Instrument(InstrumentType.STOCK, display_ticker="synthetic-share-a"),
    )
    assert not link_portfolio((entry(),), (unverified,), (a,))[0].in_portfolio


def test_portfolio_watchlist_resolve_the_same_fundamental_report():
    # Reuse only synthetic offline #72 fixtures; no new calculations or defaults.
    from test_fundamentals import identity, report_set

    from portfolio_dashboard.fundamentals import FundamentalRegistry

    checked, report = identity(), report_set()
    registry = FundamentalRegistry((checked,), (report,))
    watched = WatchlistEntry("synthetic-fundamental", "Synthetisch", checked.listing)
    assert registry.resolve(watched.instrument) is report
    assert registry.resolve(checked.listing.instrument) is report


def test_known_units_and_mapping_round_trip_without_float_conversion(tmp_path):
    from portfolio_dashboard.models import ProviderMapping

    instrument = replace(
        listing().instrument,
        quote_unit=QuoteUnit("GBP", Decimal("0.01")),
        provider_mapping=ProviderMapping("synthetic-provider", "synthetic-symbol"),
    )
    original = WatchlistEntry(
        "synthetic-unit", "Synthetisch", replace(listing(), instrument=instrument)
    )
    repo = repository(tmp_path / "private")
    repo.add(original)
    restored = repo.get(original.entry_id)
    assert restored == original
    assert restored.mapping_status is MappingStatus.VERIFIED
    assert restored.instrument.quote_unit.scale_to_currency == Decimal("0.01")


def test_stored_payload_error_does_not_disclose_private_values(tmp_path, caplog):
    directory = tmp_path / "private"
    repo = repository(directory)
    repo.add(entry())
    with sqlite3.connect(directory / "private.sqlite3") as connection:
        connection.execute(
            "UPDATE watchlist SET payload = ?", ("synthetic-private-invalid-json",)
        )
    with pytest.raises(PrivateStorageError) as error:
        repo.list()
    assert error.value.code == "data"
    assert "synthetic-private-invalid-json" not in str(error.value) + caplog.text
    assert str(directory) not in str(error.value) + caplog.text


def test_representations_do_not_expose_watchlist_values():
    record = entry()
    for text in (record.entry_id, record.label, record.instrument.instrument_id):
        assert text not in repr(record)


def test_model_requires_stock_and_nonblank_fields():
    with pytest.raises(ValueError):
        WatchlistEntry("", "synthetic")
    with pytest.raises(ValueError):
        WatchlistEntry("synthetic", " ")
    etf = VerifiedListing(
        Instrument(
            InstrumentType.ETF,
            instrument_id="synthetic-etf",
            trading_venue="synthetic-venue",
        ),
        isin="synthetic-isin",
        share_class="synthetic-class",
    )
    with pytest.raises(ValueError, match="stock"):
        WatchlistEntry("synthetic", "synthetic", etf)
