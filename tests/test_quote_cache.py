"""Synthetic provider and injected clock; no network, no system time."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from portfolio_dashboard.config import CacheTTLs
from portfolio_dashboard.market_data import (
    FxObservation,
    PriceHistory,
    PriceObservation,
    VerifiedFxPair,
)
from portfolio_dashboard.models import (
    DataWarning,
    Instrument,
    InstrumentType,
    PortfolioSnapshot,
    Provenance,
    ProviderMapping,
    QuoteUnit,
    SnapshotStatus,
    Valuation,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.quote_cache import (
    QuoteCache,
    choose_valuation,
    supports_period_return,
)

T0 = datetime(2026, 10, 22, 12, tzinfo=UTC)
EUR = QuoteUnit("EUR", Decimal(1))
USD = QuoteUnit("USD", Decimal(1))
START, END = date(2026, 10, 1), date(2026, 10, 20)


def instrument(name="A"):
    return Instrument(
        InstrumentType.STOCK,
        instrument_id=f"synthetic-{name}",
        display_ticker=name,
        provider_mapping=ProviderMapping("synthetic", f"{name}.X"),
    )


PAIR = VerifiedFxPair(USD, EUR, ProviderMapping("synthetic", "FX.X"), "SYNTH-FX")


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class FakeProvider:
    """Counts calls; `fail` makes every call return MISSING results."""

    def __init__(self, clock):
        self.clock = clock
        self.fail = False
        self.calls = 0
        self.price = Decimal(10)

    def _prov(self):
        return Provenance(
            ValuationSource.PROVIDER, self.clock(), self.clock(), provider="synthetic"
        )

    def latest_quote(self, instr):
        self.calls += 1
        if self.fail:
            return PriceObservation(
                Valuation(
                    None,
                    ValueStatus.MISSING,
                    self._prov(),
                    warnings=(DataWarning("x", "down"),),
                )
            )
        return PriceObservation(
            Valuation(self.price, ValueStatus.AVAILABLE, self._prov(), USD)
        )

    def history(self, instr, start, end, *, minimum_points=2):
        self.calls += 1
        status = ValueStatus.MISSING if self.fail else ValueStatus.AVAILABLE
        return PriceHistory(status, self._prov())

    def fx(self, pair):
        self.calls += 1
        if self.fail:
            return FxObservation(pair, None, ValueStatus.MISSING, self._prov())
        return FxObservation(pair, Decimal("1.1"), ValueStatus.AVAILABLE, self._prov())


@pytest.fixture
def env():
    clock = Clock()
    provider = FakeProvider(clock)
    return clock, provider, QuoteCache(provider, clock=clock)


def codes(item):
    return {w.code for w in item.warnings}


def test_quote_is_cached_until_ttl_then_refetched(env):
    clock, provider, cache = env
    first = cache.quote(instrument())
    clock.advance(59)
    assert cache.quote(instrument()) is first
    assert provider.calls == 1
    clock.advance(1)
    cache.quote(instrument())
    assert provider.calls == 2


def test_ttls_are_configurable_and_history_uses_its_own_ttl():
    clock = Clock()
    provider = FakeProvider(clock)
    cache = QuoteCache(provider, CacheTTLs(10, 20, 30), clock)
    cache.quote(instrument())
    cache.fx(PAIR)
    cache.history(instrument(), START, END)
    assert provider.calls == 3
    clock.advance(10)
    cache.quote(instrument())
    cache.fx(PAIR)
    cache.history(instrument(), START, END)
    assert provider.calls == 5
    clock.advance(20)
    cache.history(instrument(), START, END)
    assert provider.calls == 6


def test_manual_refresh_skips_ttl(env):
    _, provider, cache = env
    cache.quote(instrument())
    provider.price = Decimal(11)
    assert cache.quote(instrument(), refresh=True).valuation.price == Decimal(11)
    assert provider.calls == 2


def test_failure_after_success_keeps_value_labeled_cached_with_original_age(env):
    clock, provider, cache = env
    first = cache.quote(instrument())
    provider.fail = True
    clock.advance(60)
    result = cache.quote(instrument()).valuation
    assert result.price == first.valuation.price
    assert result.provenance.source is ValuationSource.CACHED_PROVIDER
    assert result.provenance.retrieved_at == T0
    assert result.provenance.observed_at == T0
    assert "cached_provider_value" in codes(result)


def test_cached_label_survives_ttl_after_failed_manual_refresh(env):
    clock, provider, cache = env
    cache.quote(instrument())
    provider.fail = True
    clock.advance(10)
    cache.quote(instrument(), refresh=True)
    clock.advance(10)
    result = cache.quote(instrument()).valuation
    assert result.provenance.source is ValuationSource.CACHED_PROVIDER
    assert provider.calls == 2


def test_backoff_blocks_calls_doubles_and_resets_on_success(env):
    clock, provider, cache = env
    cache.quote(instrument())
    provider.fail = True
    clock.advance(60)
    cache.quote(instrument())  # failure 1: backoff 15 s
    assert provider.calls == 2
    clock.advance(14)
    cache.quote(instrument(), refresh=True)  # refresh obeys backoff
    assert provider.calls == 2
    clock.advance(1)
    cache.quote(instrument())  # failure 2: backoff 30 s
    assert provider.calls == 3
    clock.advance(29)
    cache.quote(instrument())
    assert provider.calls == 3
    provider.fail = False
    clock.advance(1)
    result = cache.quote(instrument()).valuation
    assert provider.calls == 4
    assert result.provenance.source is ValuationSource.PROVIDER
    assert not result.warnings
    clock.advance(60)
    provider.fail = True
    cache.quote(instrument())  # backoff restarted at 15 s
    clock.advance(15)
    cache.quote(instrument())
    assert provider.calls == 6


def test_backoff_delay_is_capped():
    clock = Clock()
    provider = FakeProvider(clock)
    provider.fail = True
    cache = QuoteCache(provider, clock=clock, provider_failure_threshold=100)
    cache.quote(instrument())
    delays = []
    for _ in range(7):
        step = 0
        before = provider.calls
        while provider.calls == before:
            clock.advance(1)
            step += 1
            cache.quote(instrument())
        delays.append(step)
    assert delays == [15, 30, 60, 120, 240, 300, 300]


def test_history_failure_after_success_is_cached_and_flagged(env):
    clock, provider, cache = env
    cache.history(instrument(), START, END)
    provider.fail = True
    clock.advance(21_600)
    result = cache.history(instrument(), START, END)
    assert result.status is ValueStatus.AVAILABLE
    assert result.provenance.source is ValuationSource.CACHED_PROVIDER
    assert result.provenance.retrieved_at == T0
    assert "cached_provider_value" in codes(result)


def test_fx_failure_after_success_is_cached(env):
    clock, provider, cache = env
    cache.fx(PAIR)
    provider.fail = True
    clock.advance(60)
    result = cache.fx(PAIR)
    assert result.rate == Decimal("1.1")
    assert result.provenance.source is ValuationSource.CACHED_PROVIDER
    assert "cached_provider_value" in codes(result)


def snapshot(status, at):
    warnings = (
        ()
        if status is SnapshotStatus.COMPLETE
        else (DataWarning("load_failed", "failed"),)
    )
    return PortfolioSnapshot(at, status, warnings=warnings)


def test_holdings_use_five_minute_ttl_and_flag_fallback_snapshot_after_failure(env):
    clock, _, cache = env
    loads = []

    def load():
        loads.append(1)
        status = SnapshotStatus.FAILED if len(loads) > 1 else SnapshotStatus.COMPLETE
        return snapshot(status, clock())

    first = cache.holdings(load)
    clock.advance(299)
    assert cache.holdings(load) is first
    assert len(loads) == 1
    clock.advance(1)
    result = cache.holdings(load)
    assert len(loads) == 2
    assert result.status is SnapshotStatus.COMPLETE
    assert result.attempted_at == T0
    assert "cached_holdings_snapshot" in codes(result)
    assert not first.warnings


def test_restart_without_data_is_unavailable_not_a_fictitious_last_state(env):
    clock, provider, cache = env
    cache.quote(instrument())
    provider.fail = True
    fresh = QuoteCache(provider, clock=clock)  # simulated restart: empty cache
    result = fresh.quote(instrument()).valuation
    assert result.status is ValueStatus.MISSING
    assert result.price is None
    assert result.provenance.source is ValuationSource.PROVIDER
    failed = fresh.holdings(lambda: snapshot(SnapshotStatus.FAILED, clock()))
    assert failed.status is SnapshotStatus.FAILED
    assert not failed.positions


def test_bundled_quotes_fetch_only_due_keys(env):
    clock, provider, cache = env
    items = [instrument("A"), instrument("B")]
    cache.quote(items[0])
    clock.advance(30)
    result = cache.quotes(items)
    assert set(result) == set(items)
    assert provider.calls == 2  # A cached, B fetched
    cache.quotes(items)
    assert provider.calls == 2


def test_provider_wide_backoff_limits_a_failing_cycle(env):
    clock, provider, cache = env
    provider.fail = True
    items = [instrument(str(n)) for n in range(10)]
    result = cache.quotes(items)
    assert provider.calls == 3  # threshold reached, remaining keys skipped
    assert all(o.valuation.status is ValueStatus.MISSING for o in result.values())
    assert "provider_backoff" in codes(result[items[-1]].valuation)
    cache.quotes(items, refresh=True)
    assert provider.calls == 3  # manual refresh obeys provider backoff
    clock.advance(15)
    cache.quotes(items)
    assert provider.calls == 4  # one probe, then 30 s provider backoff
    clock.advance(29)
    cache.quotes(items)
    assert provider.calls == 4
    provider.fail = False
    clock.advance(1)
    cache.quotes(items)
    assert provider.calls > 4
    assert cache.quotes(items)[items[0]].valuation.status is ValueStatus.AVAILABLE


def test_provider_backoff_applies_to_fx_and_history_but_not_holdings(env):
    clock, provider, cache = env
    provider.fail = True
    cache.quotes([instrument(str(n)) for n in range(3)])
    calls = provider.calls
    assert cache.fx(PAIR).status is ValueStatus.MISSING
    assert cache.history(instrument("H"), START, END).status is ValueStatus.MISSING
    assert provider.calls == calls
    complete = cache.holdings(lambda: snapshot(SnapshotStatus.COMPLETE, clock()))
    assert complete.status is SnapshotStatus.COMPLETE


def sheet(price="9", unit=EUR, observed=None, status=ValueStatus.AVAILABLE):
    return Valuation(
        Decimal(price) if status is ValueStatus.AVAILABLE else None,
        status,
        Provenance(ValuationSource.SHEET, T0, observed),
        unit,
    )


def test_priority_provider_then_cached_provider_then_flagged_eur_sheet(env):
    clock, provider, cache = env
    live = cache.quote(instrument()).valuation
    assert choose_valuation(live, sheet()) is live
    provider.fail = True
    clock.advance(60)
    cached = cache.quote(instrument()).valuation
    assert choose_valuation(cached, sheet()) is cached
    missing = QuoteCache(provider, clock=clock).quote(instrument()).valuation
    chosen = choose_valuation(missing, sheet())
    assert chosen.provenance.source is ValuationSource.SHEET
    assert {"price_source_fallback", "sheet_value_quote_time_unknown", "x"} <= codes(
        chosen
    )
    known = choose_valuation(missing, sheet(observed=T0))
    assert "sheet_value_quote_time_unknown" not in codes(known)


def test_sheet_fallback_requires_available_eur_and_otherwise_stays_unavailable(env):
    _, provider, _ = env
    provider.fail = True
    missing = provider.latest_quote(instrument()).valuation
    assert choose_valuation(missing, None) is missing
    assert choose_valuation(missing, sheet(unit=USD)) is missing
    assert choose_valuation(missing, sheet(unit=None)) is missing
    assert (
        choose_valuation(missing, sheet(unit=QuoteUnit("EUR", Decimal("0.01"))))
        is missing
    )
    assert choose_valuation(missing, sheet(status=ValueStatus.MISSING)) is missing


def test_manual_sheet_price_creates_no_period_return(env):
    _, provider, cache = env
    live = cache.quote(instrument()).valuation
    assert supports_period_return(live)
    cached = replace(
        live,
        provenance=replace(live.provenance, source=ValuationSource.CACHED_PROVIDER),
    )
    assert supports_period_return(cached)
    assert not supports_period_return(sheet())
    provider.fail = True
    assert not supports_period_return(provider.latest_quote(instrument()).valuation)
