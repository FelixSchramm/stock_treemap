"""Loader behavior with a controlled clock, a scripted fake provider and invented data.

No network, no yfinance, no real identity. The clock only moves when a test says so.
"""

import subprocess
import sys
import threading
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from test_fundamentals_normalization import (
    EXPECTATION,
    INFO,
    RETRIEVED,
    SHARES_DATE,
    StatementTable,
    by_field,
    statements,
)

from portfolio_dashboard.fundamentals import (
    FundamentalField,
    FundamentalSource,
    IssuerKind,
    QualityStatus,
    VerifiedFundamentalIdentity,
)
from portfolio_dashboard.fundamentals_cache import (
    MemoryFundamentalsStore,
    PrivateFundamentalsStore,
    store_key,
)
from portfolio_dashboard.fundamentals_config import (
    FundamentalSubject,
    FundamentalSubjects,
)
from portfolio_dashboard.fundamentals_loader import (
    NOTE_BACKOFF,
    NOTE_DEFERRED,
    NOTE_PACKAGE_CHANGED,
    NOTE_PROVIDER_BACKOFF,
    NOTE_REFRESH_FAILED,
    NOTE_TOO_SOON,
    NOTE_UNCHANGED,
    NOTE_WINDOW_EXPIRED,
    FetchGate,
    FetchPolicy,
    FundamentalsLoader,
    FundamentalsStatus,
)
from portfolio_dashboard.fundamentals_normalization import (
    FetchFailure,
    OwnerConfirmations,
    ProviderStatements,
)
from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    QuoteUnit,
    ValueStatus,
)
from portfolio_dashboard.private_store import PrivateDatabase, PrivateStorageError

START = RETRIEVED
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


class Clock:
    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def make_subject(
    name: str = "a",
    *,
    venue: str = "synthetic-venue",
    source: str | None = None,
    issuer_kind: IssuerKind = IssuerKind.NONFINANCIAL,
    confirmations: OwnerConfirmations = OwnerConfirmations(),
) -> FundamentalSubject:
    identity = VerifiedFundamentalIdentity(
        VerifiedListing(
            Instrument(
                InstrumentType.STOCK,
                instrument_id=f"synthetic-share-{name}",
                trading_venue=venue,
                quote_unit=QuoteUnit("EUR", Decimal(1)),
            ),
            equity_form=EquityForm.SHARE,
        ),
        f"synthetic-class-{name}",
        f"synthetic-issuer-{name}",
        FundamentalSource("yfinance", source or f"SYN.{name.upper()}"),
        "synthetic-group",
        issuer_kind,
        "synthetic-identity-proof",
        START,
    )
    return FundamentalSubject(
        identity,
        EXPECTATION,
        1,
        "synthetic-class-proof",
        START,
        confirmations,
    )


def instrument(subject: FundamentalSubject) -> Instrument:
    return subject.identity.listing.instrument


class FakeProvider:
    """Scripted answers; each step is a callable ``clock_time -> ProviderStatements``."""

    def __init__(self, clock: Clock, *script) -> None:
        self.clock = clock
        self.script = list(script)
        self.calls: list[str] = []

    def fetch(self, identity):
        self.calls.append(identity.source.entity_id)
        step = self.script.pop(0) if self.script else good()
        if isinstance(step, BaseException):
            raise step
        return step(self.clock.now)


def good(**overrides):
    return lambda now: statements(retrieved_at=now, **overrides)


def failed(dataset="request", code="provider_error", **overrides):
    return lambda now: ProviderStatements(
        now, failures=(FetchFailure(dataset, code),), **overrides
    )


def partial_without_balance(now):
    full = statements(retrieved_at=now)
    return replace(
        full,
        annual_balance=None,
        failures=(FetchFailure("annual_balance", "timeout"),),
    )


def build(
    *subjects,
    script=(),
    clock=None,
    policy=FetchPolicy(),
    store=None,
    gate=None,
    package="1.7.0",
    sleep=None,
):
    clock = clock or Clock()
    provider = FakeProvider(clock, *script)
    sleeps: list[float] = []
    loader = FundamentalsLoader(
        FundamentalSubjects(tuple(subjects)),
        provider,
        store or MemoryFundamentalsStore(),
        policy,
        clock=clock,
        sleep=sleep or sleeps.append,
        package_version=package,
        gate=gate or FetchGate(),
    )
    loader.sleeps = sleeps
    return loader, provider, clock


# --- cache hit, expiry and forced refresh -----------------------------------------


def test_first_load_fetches_once_and_the_window_serves_later_loads_from_the_cache():
    subject = make_subject()
    loader, provider, clock = build(subject)
    first = loader.load([instrument(subject)])
    entry = first.entry(instrument(subject))
    assert (first.provider_calls, entry.status) == (1, FundamentalsStatus.CURRENT)
    assert entry.notes == () and entry.failures == ()
    assert entry.last_checked_at == START and entry.retrieved_at == START
    clock.advance(DAY - timedelta(seconds=1))
    again = loader.load([instrument(subject)])
    assert again.provider_calls == 0 and provider.calls == ["SYN.A"]
    assert again.entry(instrument(subject)).status is FundamentalsStatus.CURRENT
    assert again.entry(instrument(subject)).report_set == entry.report_set


def test_expired_window_triggers_exactly_one_new_retrieval():
    subject = make_subject()
    loader, provider, clock = build(subject)
    loader.load([instrument(subject)])
    clock.advance(DAY)  # Exactly at the limit the window is over.
    bundle = loader.load([instrument(subject)])
    assert bundle.provider_calls == 1 and len(provider.calls) == 2
    entry = bundle.entry(instrument(subject))
    assert entry.status is FundamentalsStatus.CURRENT
    assert entry.last_checked_at == START + DAY


def test_configured_window_is_used():
    subject = make_subject()
    loader, provider, clock = build(subject, policy=FetchPolicy(ttl_seconds=600))
    loader.load([instrument(subject)])
    clock.advance(timedelta(seconds=599))
    assert loader.load([instrument(subject)]).provider_calls == 0
    clock.advance(timedelta(seconds=1))
    assert loader.load([instrument(subject)]).provider_calls == 1


def test_forced_refresh_skips_the_window_but_respects_the_minimum_age():
    subject = make_subject()
    loader, provider, clock = build(subject)
    loader.load([instrument(subject)])
    clock.advance(timedelta(minutes=30))
    too_soon = loader.load([instrument(subject)], refresh=True)
    assert too_soon.provider_calls == 0
    assert NOTE_TOO_SOON in too_soon.entry(instrument(subject)).notes
    assert too_soon.entry(instrument(subject)).status is FundamentalsStatus.CURRENT
    clock.advance(timedelta(minutes=30))
    forced = loader.load([instrument(subject)], refresh=True)
    assert forced.provider_calls == 1 and len(provider.calls) == 2


def test_forced_refresh_obeys_backoff():
    subject = make_subject()
    loader, provider, clock = build(subject, script=[good(), failed()])
    loader.load([instrument(subject)])
    clock.advance(2 * HOUR)
    assert loader.load([instrument(subject)], refresh=True).provider_calls == 1
    clock.advance(timedelta(seconds=10))  # Inside the 300 s backoff.
    blocked = loader.load([instrument(subject)], refresh=True)
    assert blocked.provider_calls == 0
    assert NOTE_BACKOFF in blocked.entry(instrument(subject)).notes
    assert len(provider.calls) == 2


# --- failure, backoff and recovery --------------------------------------------------


def test_backoff_doubles_up_to_the_cap_and_blocks_between_attempts():
    subject = make_subject()
    policy = FetchPolicy(backoff_base_seconds=100, backoff_max_seconds=250)
    loader, provider, clock = build(subject, script=[failed()] * 4, policy=policy)
    waits = []
    for _ in range(4):
        entry = loader.load([instrument(subject)]).entry(instrument(subject))
        waits.append((entry.retry_after - clock.now).total_seconds())
        clock.advance(timedelta(seconds=waits[-1] - 1))
        assert loader.load([instrument(subject)]).provider_calls == 0
        clock.advance(timedelta(seconds=1))
    assert waits == [100, 200, 250, 250]
    assert len(provider.calls) == 4


def test_restriction_is_not_retried_and_waits_much_longer():
    subject = make_subject()
    loader, provider, clock = build(
        subject, script=[failed("info", "access_restricted")]
    )
    entry = loader.load([instrument(subject)]).entry(instrument(subject))
    assert entry.status is FundamentalsStatus.UNAVAILABLE
    assert entry.failures == ("info_access_restricted",)
    assert entry.retry_after - clock.now == timedelta(hours=1)
    clock.advance(timedelta(minutes=59))
    assert loader.load([instrument(subject)]).provider_calls == 0
    assert len(provider.calls) == 1  # Not even one in-cycle retry.


def test_a_restriction_stops_every_other_retrieval_in_the_process():
    first, second = make_subject("a"), make_subject("b")
    gate = FetchGate()
    loader, provider, clock = build(
        first, second, script=[failed("info", "access_restricted")], gate=gate
    )
    bundle = loader.load([instrument(first), instrument(second)])
    assert bundle.provider_calls == 1 and provider.calls == ["SYN.A"]
    other = bundle.entry(instrument(second))
    assert other.status is FundamentalsStatus.UNAVAILABLE
    assert NOTE_PROVIDER_BACKOFF in other.notes
    # A second loader (another session) shares the gate and also stays away.
    sibling, sibling_provider, _ = build(first, second, gate=gate, clock=clock)
    assert sibling.load([instrument(second)]).provider_calls == 0
    assert sibling_provider.calls == []
    clock.advance(timedelta(hours=1))
    assert loader.load([instrument(second)]).provider_calls == 1


def test_partial_failure_keeps_the_last_complete_state_flagged_and_never_merges():
    subject = make_subject()
    loader, provider, clock = build(subject, script=[good(), partial_without_balance])
    good_entry = loader.load([instrument(subject)]).entry(instrument(subject))
    clock.advance(DAY)
    bundle = loader.load([instrument(subject)])
    entry = bundle.entry(instrument(subject))
    assert entry.status is FundamentalsStatus.CACHED
    assert {NOTE_WINDOW_EXPIRED, NOTE_REFRESH_FAILED} <= set(entry.notes)
    assert entry.stale and entry.failures == ("annual_balance_timeout",)
    # Exactly the earlier complete retrieval: its time, its numbers, its report set.
    assert entry.retrieved_at == START and entry.last_checked_at == START
    assert entry.last_attempt_at == clock.now
    assert entry.report_set == good_entry.report_set
    assert entry.retrieval_age(clock.now) == DAY


def test_new_fields_are_not_mixed_into_the_old_statements():
    subject = make_subject()
    changed = good(
        annual_income=StatementTable(
            {
                label: {end: value * 2 for end, value in cells.items()}
                for label, cells in statements().annual_income.rows.items()
            }
        ),
        annual_cashflow=None,  # The new answer lacks a whole dataset.
    )
    loader, _, clock = build(subject, script=[good(), changed])
    before = loader.load([instrument(subject)]).entry(instrument(subject))
    clock.advance(DAY)
    after = loader.load([instrument(subject)]).entry(instrument(subject))
    assert "annual_cashflow_empty" in after.failures
    ebit_before = by_field(before.normalized, FundamentalField.EBIT)
    ebit_after = by_field(after.normalized, FundamentalField.EBIT)
    assert [o.value for o in ebit_after] == [o.value for o in ebit_before]


def test_recovery_after_an_outage_clears_the_failure_state():
    subject = make_subject()
    loader, provider, clock = build(subject, script=[good(), failed(), failed()])
    loader.load([instrument(subject)])
    clock.advance(DAY)
    outage = loader.load([instrument(subject)]).entry(instrument(subject))
    assert outage.status is FundamentalsStatus.CACHED
    clock.advance(timedelta(seconds=300))
    still = loader.load([instrument(subject)]).entry(instrument(subject))
    assert still.status is FundamentalsStatus.CACHED
    clock.advance(timedelta(seconds=600))
    recovered = loader.load([instrument(subject)]).entry(instrument(subject))
    assert recovered.status is FundamentalsStatus.CURRENT
    assert recovered.failures == () and recovered.last_attempt_at is None
    assert recovered.last_checked_at == clock.now
    assert recovered.notes == (NOTE_UNCHANGED,)  # Same content as before the outage.
    assert len(provider.calls) == 4


def test_without_any_success_no_state_is_invented_and_a_restart_does_not_either():
    subject = make_subject()
    loader, provider, clock = build(subject, script=[failed("info", "timeout")])
    entry = loader.load([instrument(subject)]).entry(instrument(subject))
    assert entry.status is FundamentalsStatus.UNAVAILABLE
    assert entry.normalized is None and entry.report_set is None
    assert entry.retrieved_at is None and entry.last_checked_at is None
    assert entry.latest_period_end is None and entry.bridge_date is None
    assert entry.issues_at(clock.now) == ()
    # "Restart": a new loader and an empty store, same private mapping.
    fresh, fresh_provider, _ = build(subject, script=[failed()], clock=clock)
    again = fresh.load([instrument(subject)]).entry(instrument(subject))
    assert again.status is FundamentalsStatus.UNAVAILABLE and again.report_set is None


def test_a_throttled_unchanged_world_never_serves_a_state_after_a_package_change():
    subject = make_subject()
    store = MemoryFundamentalsStore()
    old, _, clock = build(subject, store=store, package="1.7.0")
    old.load([instrument(subject)])
    new, provider, _ = build(
        subject, store=store, package="1.8.0", clock=clock, script=[failed()]
    )
    entry = new.load([instrument(subject)]).entry(instrument(subject))
    # Due despite the open window, failed, so the old state is shown flagged.
    assert provider.calls == ["SYN.A"]
    assert entry.status is FundamentalsStatus.CACHED
    assert {NOTE_PACKAGE_CHANGED, NOTE_REFRESH_FAILED} <= set(entry.notes)
    assert entry.package_version == "1.7.0"


def test_an_answer_that_contradicts_the_owner_does_not_replace_the_old_state():
    subject = make_subject()
    wrong_currency = good(info={**INFO, "financialCurrency": "USD"})
    loader, _, clock = build(subject, script=[good(), wrong_currency])
    first = loader.load([instrument(subject)]).entry(instrument(subject))
    clock.advance(DAY)
    entry = loader.load([instrument(subject)]).entry(instrument(subject))
    assert entry.status is FundamentalsStatus.CACHED
    assert "numbers_withheld" in entry.failures
    assert entry.report_set == first.report_set


def renamed_income(now):
    """A changed layout: the tables are present but no known row label matches."""
    income = statements().annual_income
    renamed = StatementTable(
        {label.replace(" ", ""): cells for label, cells in income.rows.items()}
    )
    return statements(retrieved_at=now, annual_income=renamed)


def test_an_unrecognizable_layout_does_not_replace_the_old_state():
    subject = make_subject()
    loader, _, clock = build(subject, script=[good(), renamed_income])
    first = loader.load([instrument(subject)]).entry(instrument(subject))
    clock.advance(DAY)
    entry = loader.load([instrument(subject)]).entry(instrument(subject))
    assert entry.status is FundamentalsStatus.CACHED
    assert "no_usable_base" in entry.failures
    assert "annual_income_unexpected_schema" in entry.failures
    assert entry.report_set == first.report_set and entry.retrieved_at == START


def test_an_unrecognizable_first_layout_stays_unavailable():
    subject = make_subject()
    loader, _, _ = build(subject, script=[renamed_income])
    entry = loader.load([instrument(subject)]).entry(instrument(subject))
    assert entry.status is FundamentalsStatus.UNAVAILABLE
    assert "annual_income_unexpected_schema" in entry.failures
    assert entry.report_set is None and entry.last_checked_at is None


def test_first_answer_that_contradicts_the_owner_stays_unavailable():
    subject = make_subject()
    loader, _, _ = build(
        subject, script=[good(info={**INFO, "financialCurrency": "USD"})]
    )
    entry = loader.load([instrument(subject)]).entry(instrument(subject))
    assert entry.status is FundamentalsStatus.UNAVAILABLE
    assert "numbers_withheld" in entry.failures and entry.report_set is None


def test_a_raising_provider_is_a_sanitized_failure_with_backoff():
    subject = make_subject()
    loader, provider, clock = build(
        subject, script=[RuntimeError("SECRET-SYMBOL-AND-URL")]
    )
    bundle = loader.load([instrument(subject)])
    entry = bundle.entry(instrument(subject))
    assert entry.failures == ("request_provider_error",)
    assert "SECRET" not in repr(entry) + repr(bundle)
    assert loader.load([instrument(subject)]).provider_calls == 0


# --- business age is separate from the retrieval age ---------------------------------


def test_a_new_retrieval_does_not_make_old_reports_recent():
    subject = make_subject(confirmations=OwnerConfirmations(True, True, True))
    clock = Clock(datetime(2026, 8, 1, 9, 0, tzinfo=UTC))
    loader, _, _ = build(subject, clock=clock)  # Provider re-delivers the FY2024 set.
    entry = loader.load([instrument(subject)]).entry(instrument(subject))
    assert entry.status is FundamentalsStatus.CURRENT
    assert entry.last_checked_at == clock.now  # The retrieval is fresh ...
    assert entry.latest_period_end == date(2024, 12, 31)  # ... the reports are not.
    issues = {(i.status, i.code) for i in entry.issues_at(clock.now)}
    assert (QualityStatus.STALE, "annual_report_age") in issues
    assert (QualityStatus.STALE, "bridge_age") in issues
    assert (QualityStatus.STALE, "share_count_age") in issues


def test_unknown_publication_date_stays_unknown_after_every_retrieval():
    subject = make_subject()
    loader, _, clock = build(subject)
    loader.load([instrument(subject)])
    clock.advance(2 * DAY)
    entry = loader.load([instrument(subject)]).entry(instrument(subject))
    observations = entry.normalized.observations
    assert all(o.provenance.published_at is None for o in observations)
    assert all(o.provenance.reported_at is None for o in observations)
    assert any(
        i.status is QualityStatus.UNKNOWN_PUBLICATION
        for i in entry.issues_at(clock.now)
    )


def test_identical_content_keeps_its_delivery_time_and_input_version():
    subject = make_subject()
    loader, _, clock = build(subject)
    first = loader.load([instrument(subject)]).entry(instrument(subject))
    clock.advance(DAY)
    second = loader.load([instrument(subject)]).entry(instrument(subject))
    assert NOTE_UNCHANGED in second.notes
    assert second.retrieved_at == first.retrieved_at == START
    assert second.last_checked_at == START + DAY
    assert second.report_set.input_version == first.report_set.input_version
    assert second.report_set == first.report_set


def test_changed_content_gets_a_new_delivery_time_and_input_version():
    subject = make_subject()
    more_shares = good(dated_shares=((SHARES_DATE, Decimal(10_100_000)),))
    loader, _, clock = build(subject, script=[good(), more_shares])
    first = loader.load([instrument(subject)]).entry(instrument(subject))
    clock.advance(DAY)
    second = loader.load([instrument(subject)]).entry(instrument(subject))
    assert NOTE_UNCHANGED not in second.notes
    assert second.retrieved_at == START + DAY
    assert second.report_set.input_version != first.report_set.input_version


def test_owner_confirmations_change_the_input_version_not_the_retrieval():
    plain, confirmed = (
        make_subject(),
        make_subject(confirmations=OwnerConfirmations(True, False, False)),
    )
    store = MemoryFundamentalsStore()
    one, _, clock = build(plain, store=store)
    first = one.load([instrument(plain)]).entry(instrument(plain))
    two, provider, _ = build(confirmed, store=store, clock=clock)
    second = two.load([instrument(confirmed)]).entry(instrument(confirmed))
    assert provider.calls == []  # Same cached retrieval ...
    assert second.report_set.input_version != first.report_set.input_version


# --- deduplication, mapping and limits -------------------------------------------------


def test_portfolio_and_watchlist_listings_of_one_security_share_one_retrieval():
    depot = make_subject("a", venue="venue-depot", source="SYN.SHARED")
    watch = make_subject("a", venue="venue-watchlist", source="SYN.SHARED")
    watch = replace(
        watch,
        identity=replace(
            watch.identity,
            listing=replace(
                watch.identity.listing,
                instrument=replace(
                    watch.identity.listing.instrument, provider_mapping=None
                ),
            ),
        ),
    )
    loader, provider, _ = build(depot, watch)
    bundle = loader.load([instrument(depot), instrument(watch)])
    assert bundle.provider_calls == 1 and provider.calls == ["SYN.SHARED"]
    left, right = bundle.entry(instrument(depot)), bundle.entry(instrument(watch))
    assert left.status is right.status is FundamentalsStatus.CURRENT
    assert left.report_set.input_version == right.report_set.input_version
    # Separate calls (Watchlist view after the portfolio view) hit the cache.
    assert loader.load([instrument(watch)]).provider_calls == 0
    assert len(provider.calls) == 1


def test_duplicate_instruments_in_one_call_are_one_entry_and_one_retrieval():
    subject = make_subject()
    loader, provider, _ = build(subject)
    bundle = loader.load([instrument(subject), instrument(subject)])
    assert len(bundle.entries) == 1 and bundle.provider_calls == 1


def test_unmapped_and_unsupported_instruments_trigger_no_retrieval():
    mapped = make_subject("a")
    bank = make_subject("bank", issuer_kind=IssuerKind.BANK)
    stranger = Instrument(
        InstrumentType.STOCK,
        instrument_id="synthetic-unknown",
        trading_venue="synthetic-venue",
        quote_unit=QuoteUnit("EUR", Decimal(1)),
    )
    loader, provider, _ = build(mapped, bank)
    bundle = loader.load([stranger, instrument(bank)])
    assert bundle.provider_calls == 0 and provider.calls == []
    assert bundle.entry(stranger).status is FundamentalsStatus.UNMAPPED
    unsupported = bundle.entry(instrument(bank))
    assert unsupported.status is FundamentalsStatus.UNSUPPORTED
    assert unsupported.notes == ("instrument_model",)
    assert unsupported.report_set is None


def test_batch_limit_defers_the_rest_and_the_next_call_continues():
    subjects = [make_subject(name) for name in "abc"]
    loader, provider, clock = build(
        *subjects, policy=FetchPolicy(max_fetches_per_load=2)
    )
    items = [instrument(s) for s in subjects]
    first = loader.load(items)
    assert first.provider_calls == 2
    deferred = first.entry(items[2])
    assert deferred.status is FundamentalsStatus.UNAVAILABLE
    assert deferred.notes == (NOTE_DEFERRED,)
    second = loader.load(items)
    assert second.provider_calls == 1 and provider.calls == ["SYN.A", "SYN.B", "SYN.C"]
    assert all(e.status is FundamentalsStatus.CURRENT for e in second.entries.values())


def test_never_loaded_securities_come_before_refreshing_old_ones():
    old, new = make_subject("a"), make_subject("b")
    loader, provider, clock = build(
        old, new, policy=FetchPolicy(max_fetches_per_load=1)
    )
    loader.load([instrument(old)])
    clock.advance(2 * DAY)
    loader.load([instrument(old), instrument(new)])
    assert provider.calls == ["SYN.A", "SYN.B"]


def test_pauses_separate_retrievals_but_not_the_first():
    subjects = [make_subject(name) for name in "ab"]
    loader, provider, clock = build(*subjects, policy=FetchPolicy(pause_seconds=3))
    loader.load([instrument(s) for s in subjects])
    assert loader.sleeps == [
        3.0
    ]  # Clock stood still: the full pause before the second.
    clock.advance(2 * HOUR)  # Past the minimum age of a forced refresh.
    loader.load([instrument(s) for s in subjects], refresh=True)
    assert loader.sleeps == [3.0, 3.0]  # Only the second of the pair waits again.


def test_concurrent_sessions_retrieve_once():
    subject = make_subject()
    gate, store = FetchGate(), MemoryFundamentalsStore()
    loaders = [
        build(subject, gate=gate, store=store)[0],
        build(subject, gate=gate, store=store)[0],
    ]
    release = threading.Event()
    calls = []

    class Slow:
        def fetch(self, identity):
            calls.append(identity.source.entity_id)
            release.wait(timeout=5)
            return statements(retrieved_at=START)

    for loader in loaders:
        loader._provider = Slow()
    results = []
    threads = [
        threading.Thread(
            target=lambda each=each: results.append(each.load([instrument(subject)]))
        )
        for each in loaders
    ]
    for thread in threads:
        thread.start()
    release.set()
    for thread in threads:
        thread.join(timeout=10)
    assert calls == ["SYN.A"]
    assert [r.entry(instrument(subject)).status for r in results] == [
        FundamentalsStatus.CURRENT
    ] * 2


def test_policy_validation_and_default_window():
    assert FetchPolicy().ttl_seconds == 86_400
    for bad in (
        {"ttl_seconds": 0},
        {"pause_seconds": -1},
        {"max_fetches_per_load": 0},
        {"backoff_base_seconds": 10, "backoff_max_seconds": 5},
    ):
        with pytest.raises(ValueError):
            FetchPolicy(**bad)


# --- access: owner, loopback, demo -------------------------------------------------


class GuardedStore(MemoryFundamentalsStore):
    """Memory store whose access check can be switched, counting every call."""

    def __init__(self) -> None:
        super().__init__()
        self.allowed = True
        self.checks = 0
        self.reads = 0

    def require_access(self) -> None:
        self.checks += 1
        if not self.allowed:
            raise PrivateStorageError("access")

    def get(self, key):
        self.reads += 1
        return super().get(key)


def test_refused_access_reads_nothing_and_retrieves_nothing():
    subject = make_subject()
    store = GuardedStore()
    store.allowed = False
    loader, provider, _ = build(subject, store=store)
    with pytest.raises(PrivateStorageError) as error:
        loader.load([instrument(subject)])
    assert error.value.code == "access"
    assert store.reads == 0 and provider.calls == []


def test_access_is_checked_again_right_before_the_provider_call():
    subject = make_subject()
    store = GuardedStore()
    loader, provider, _ = build(subject, store=store)

    original = store.require_access

    def revoked_after_first_check():
        original()
        if store.checks >= 1:
            store.allowed = False  # The session ends between the checks.

    store.require_access = revoked_after_first_check
    with pytest.raises(PrivateStorageError):
        loader.load([instrument(subject)])
    assert provider.calls == []
    assert store.checks == 2


def private_loader(tmp_path, mode, *, user=None, address="127.0.0.1", **kwargs):
    from portfolio_dashboard.config import (
        AppConfig,
        CacheTTLs,
        LoginConfig,
        Provider,
        SourceConfig,
    )

    login = LoginConfig(
        enabled=True,
        owner_issuer="https://accounts.google.com",
        owner_subject="synthetic-owner",
        client_id="synthetic-client",
    )
    directory = tmp_path / "private"
    database = PrivateDatabase(
        lambda: AppConfig(
            mode, SourceConfig(), Provider.SYNTHETIC, CacheTTLs(), login, str(directory)
        ),
        user=lambda: user or {},
        server_address=lambda: address,
    )
    subject = kwargs.pop("subject", None) or make_subject()
    loader, provider, clock = build(
        subject, store=PrivateFundamentalsStore(database), **kwargs
    )
    return loader, provider, clock, subject, directory


@pytest.mark.parametrize(
    "case",
    ["anonymous", "foreign", "expired", "demo", "non-loopback"],
)
def test_foreign_sessions_demo_and_remote_local_never_reach_provider_or_disk(
    tmp_path, monkeypatch, case
):
    import portfolio_dashboard.auth as auth
    from portfolio_dashboard.config import Mode

    now = 2_000_000_000
    monkeypatch.setattr(auth.time, "time", lambda: now)
    owner = {
        "is_logged_in": True,
        "iss": "https://accounts.google.com",
        "sub": "synthetic-owner",
        "aud": "synthetic-client",
        "iat": now - 60,
        "exp": now + 60,
    }
    mode, user, address = Mode.PRODUCTION, {}, "127.0.0.1"
    if case == "foreign":
        user = owner | {"sub": "synthetic-other"}
    elif case == "expired":
        user = owner | {"exp": now}
    elif case == "demo":
        mode = Mode.DEMO
    elif case == "non-loopback":
        mode, address = Mode.LOCAL, "0.0.0.0"
    loader, provider, _, subject, directory = private_loader(
        tmp_path, mode, user=user, address=address
    )
    with pytest.raises(PrivateStorageError) as error:
        loader.load([instrument(subject)])
    assert error.value.code in {"access", "local"}
    assert provider.calls == []
    assert not directory.exists()


def test_owner_session_loads_persists_and_survives_a_restart(tmp_path, monkeypatch):
    import portfolio_dashboard.auth as auth
    from portfolio_dashboard.config import Mode

    now = 2_000_000_000
    monkeypatch.setattr(auth.time, "time", lambda: now)
    owner = {
        "is_logged_in": True,
        "iss": "https://accounts.google.com",
        "sub": "synthetic-owner",
        "aud": "synthetic-client",
        "iat": now - 60,
        "exp": now + 60,
    }
    loader, provider, clock, subject, directory = private_loader(
        tmp_path, Mode.PRODUCTION, user=owner
    )
    first = loader.load([instrument(subject)]).entry(instrument(subject))
    assert provider.calls == ["SYN.A"] and first.status is FundamentalsStatus.CURRENT
    # Restart: new loader, new gate, new database handle, same protected directory.
    clock.advance(HOUR)
    again, provider2, _, _, _ = private_loader(
        tmp_path, Mode.PRODUCTION, user=owner, subject=subject, clock=clock
    )
    restored = again.load([instrument(subject)]).entry(instrument(subject))
    assert provider2.calls == []
    assert restored.status is FundamentalsStatus.CURRENT
    assert restored.report_set == first.report_set
    assert restored.last_checked_at == START
    assert restored.retrieval_age(clock.now) == HOUR
    # No identifier is a column or a file name; the file is owner-only.
    assert [p.name for p in directory.iterdir()] == ["private.sqlite3"]
    assert (directory / "private.sqlite3").stat().st_mode & 0o077 == 0
    assert store_key(subject.fetch_key) not in str(directory)


def test_local_mode_on_loopback_works_without_login(tmp_path):
    from portfolio_dashboard.config import Mode

    loader, provider, _, subject, _ = private_loader(tmp_path, Mode.LOCAL)
    entry = loader.load([instrument(subject)]).entry(instrument(subject))
    assert entry.status is FundamentalsStatus.CURRENT and provider.calls == ["SYN.A"]


def test_missing_protected_directory_means_no_retrieval(tmp_path):
    from portfolio_dashboard.config import (
        AppConfig,
        CacheTTLs,
        LoginConfig,
        Mode,
        Provider,
        SourceConfig,
    )

    database = PrivateDatabase(
        lambda: AppConfig(
            Mode.LOCAL,
            SourceConfig(),
            Provider.SYNTHETIC,
            CacheTTLs(),
            LoginConfig(),
            None,
        ),
        server_address=lambda: "127.0.0.1",
    )
    subject = make_subject()
    loader, provider, _ = build(subject, store=PrivateFundamentalsStore(database))
    with pytest.raises(PrivateStorageError) as error:
        loader.load([instrument(subject)])
    assert error.value.code == "path" and provider.calls == []


# --- demo -----------------------------------------------------------------------------------


def test_demo_loader_is_synthetic_offline_and_writes_nothing(tmp_path):
    code = (
        "import os, sys\n"
        f"os.chdir({str(tmp_path)!r})\n"
        "from portfolio_dashboard.fundamentals_demo import (\n"
        "    DEMO_FUNDAMENTALS_INSTRUMENT, demo_fundamentals_loader)\n"
        "bundle = demo_fundamentals_loader().load([DEMO_FUNDAMENTALS_INSTRUMENT])\n"
        "entry = bundle.entry(DEMO_FUNDAMENTALS_INSTRUMENT)\n"
        "assert entry.status.value == 'current' and entry.report_set is not None\n"
        "assert bundle.provider_calls == 1\n"
        "assert 'yfinance' not in sys.modules\n"
        "assert not os.listdir('.')\n"
    )
    subprocess.run([sys.executable, "-I", "-c", code], check=True)


def test_demo_loader_has_the_real_interface_and_no_gaps_to_hide():
    from portfolio_dashboard.fundamentals_demo import (
        DEMO_FUNDAMENTALS_INSTRUMENT,
        demo_fundamentals_loader,
    )

    loader = demo_fundamentals_loader()
    first = loader.load([DEMO_FUNDAMENTALS_INSTRUMENT])
    entry = first.entry(DEMO_FUNDAMENTALS_INSTRUMENT)
    assert entry.latest_period_end == date(2025, 12, 31)
    # The demo is deliberately unconfirmed and incomplete like real owner input.
    codes = {i.code for i in entry.issues_at(entry.last_checked_at + DAY) if i.blocking}
    assert {"ebit_definition", "operating_tax_rate"} <= codes
    assert loader.load([DEMO_FUNDAMENTALS_INSTRUMENT]).provider_calls == 0


def test_entries_do_not_print_numbers_or_identifiers():
    subject = make_subject()
    loader, _, _ = build(subject)
    bundle = loader.load([instrument(subject)])
    text = repr(bundle) + repr(bundle.entry(instrument(subject)))
    assert "synthetic" not in text and "SYN." not in text and "150000000" not in text


def test_observation_values_are_available_numbers_not_defaults():
    subject = make_subject()
    loader, _, _ = build(subject)
    entry = loader.load([instrument(subject)]).entry(instrument(subject))
    ebit = by_field(entry.normalized, FundamentalField.EBIT)
    assert all(o.status is ValueStatus.AVAILABLE for o in ebit)
