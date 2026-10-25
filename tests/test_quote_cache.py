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


DOWN = DataWarning("provider_failure", "down")


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class FakeProvider:
    """Synthetic outages, permanent symbol failures, and missing mappings."""

    def __init__(self, clock):
        self.clock = clock
        self.fail = False
        self.rejected = set()
        self.unmapped = set()
        self.calls = 0
        self.price = Decimal(10)

    def _prov(self):
        return Provenance(
            ValuationSource.PROVIDER, self.clock(), self.clock(), provider="synthetic"
        )

    def latest_quote(self, instr):
        self.calls += 1
        if instr in self.unmapped:
            return PriceObservation(
                Valuation(
                    None,
                    ValueStatus.MISSING,
                    self._prov(),
                    warnings=(DataWarning("unverified_mapping", "no mapping"),),
                )
            )
        if self.fail or instr in self.rejected:
            return PriceObservation(
                Valuation(
                    None,
                    ValueStatus.MISSING,
                    self._prov(),
                    warnings=(DOWN,),
                )
            )
        return PriceObservation(
            Valuation(self.price, ValueStatus.AVAILABLE, self._prov(), USD)
        )

    def history(self, instr, start, end, *, minimum_points=2):
        self.calls += 1
        if instr in self.unmapped:
            return PriceHistory(
                ValueStatus.MISSING,
                self._prov(),
                warnings=(DataWarning("no_provider_coverage", "no coverage"),),
            )
        if self.fail or instr in self.rejected:
            return PriceHistory(ValueStatus.MISSING, self._prov(), warnings=(DOWN,))
        return PriceHistory(ValueStatus.AVAILABLE, self._prov())

    def fx(self, pair):
        self.calls += 1
        if self.fail:
            return FxObservation(
                pair, None, ValueStatus.MISSING, self._prov(), (DOWN,)
            )
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
    for _ in range(40):
        clock.advance(15)
        before = provider.calls
        cache.quotes(items, refresh=True)
        assert provider.calls - before <= 1  # bounded even as probes change keys
    provider.fail = False
    clock.advance(300)
    before = provider.calls
    recovered = cache.quotes(items)
    assert provider.calls == before + len(items)
    assert all(o.valuation.status is ValueStatus.AVAILABLE for o in recovered.values())
    assert all(
        o.valuation.provenance.source is ValuationSource.PROVIDER
        for o in recovered.values()
    )


@pytest.mark.parametrize("refresh", [False, True])
def test_rejected_symbols_do_not_starve_healthy_bundled_quotes(env, refresh):
    clock, provider, cache = env
    rejected = [instrument(name) for name in "UVW"]
    stock = instrument("S")
    items = [*rejected, stock]
    provider.rejected = set(rejected)
    first = cache.quotes(iter(items), refresh=refresh)
    assert "provider_backoff" in codes(first[stock].valuation)
    assert provider.calls == 3

    for _ in range(29):
        clock.advance(60)
        result = cache.quotes(iter(items), refresh=refresh)
        assert list(result) == items  # fetch ordering must not reorder the result
        healthy = result[stock].valuation
        assert healthy.status is ValueStatus.AVAILABLE
        assert healthy.provenance.source is ValuationSource.PROVIDER
        assert healthy.provenance.retrieved_at == clock()
        assert not healthy.warnings
        assert all(
            result[item].valuation.status is ValueStatus.MISSING for item in rejected
        )


def test_rejected_symbols_do_not_block_fx_and_history_after_the_quote_bundle(env):
    clock, provider, cache = env
    rejected = [instrument(name) for name in "UVW"]
    stock = instrument("S")
    provider.rejected = set(rejected)
    for cycle in range(30):  # refresh.py order: quotes, then FX, then history
        quotes = cache.quotes([*rejected, stock])
        fx = cache.fx(PAIR)
        history = cache.history(stock, START, END)
        if cycle:  # a cold start cannot tell rejected symbols from an outage
            assert quotes[stock].valuation.status is ValueStatus.AVAILABLE
            for result in (fx, history):
                assert result.status is ValueStatus.AVAILABLE
                assert result.provenance.source is ValuationSource.PROVIDER
                assert "provider_backoff" not in codes(result)
        clock.advance(300)


def test_provider_wide_backoff_rearms_after_partial_recovery(env):
    clock, provider, cache = env
    items = [instrument(str(n)) for n in range(10)]
    provider.fail = True
    for _ in range(8):  # all 10 keys accumulate failures == 1 during the outage
        cache.quotes(items)
        clock.advance(300)
    assert provider.calls == 10

    # Case 1: one probe succeeds in quotes(), then provider fails on remaining keys
    provider.fail = False
    provider.rejected = set(items[1:])
    before = provider.calls
    first_partial = cache.quotes(items)
    assert provider.calls == before + 4  # 1 recovery probe + 3 failures -> backoff
    assert first_partial[items[0]].valuation.status is ValueStatus.AVAILABLE
    assert "provider_backoff" in codes(cache.fx(PAIR))
    assert provider.calls == before + 4

    # Case 2: all 10 keys have failures >= 1 again; one standalone probe succeeds
    # (resets _provider_failures to 0 while items[1:] still have failures >= 1),
    # then provider fails again on the next bundle
    provider.rejected = set()
    provider.fail = True
    clock.advance(300)
    cache.quotes(items)  # items[0] fails again, so all 10 keys have failures >= 1
    clock.advance(300)
    provider.fail = False
    assert cache.quote(items[0]).valuation.status is ValueStatus.AVAILABLE
    provider.fail = True
    clock.advance(300)
    before = provider.calls
    cache.quotes(items)
    assert provider.calls == before + 3  # breaker rearms after 3 failures, not 10
    assert "provider_backoff" in codes(cache.fx(PAIR))
    assert provider.calls == before + 3

    # Subsequent cycles stay bounded to a single recovery probe per window
    clock.advance(300)
    before = provider.calls
    cache.quotes(items)
    assert provider.calls == before + 1


def test_unmapped_instruments_do_not_trigger_provider_backoff(env):
    clock, provider, cache = env
    unmapped = [instrument(name) for name in "UVW"]
    stock = instrument("S")
    provider.unmapped = set(unmapped)
    for _ in range(3):
        result = cache.quotes([*unmapped, stock])
        assert result[stock].valuation.status is ValueStatus.AVAILABLE
        assert "provider_backoff" not in codes(result[stock].valuation)
        clock.advance(60)
    before = provider.calls
    for days in range(3):  # distinct keys must each reach the provider adapter
        cache.history(unmapped[0], START + timedelta(days=days), END)
    fresh = cache.quote(instrument("NEW")).valuation
    assert provider.calls == before + 4
    assert fresh.status is ValueStatus.AVAILABLE
    assert fresh.provenance.source is ValuationSource.PROVIDER
    assert not {"provider_backoff", "cached_provider_value"} & codes(fresh)


def test_stock_is_fetched_again_after_outage_with_unmapped_instrument_before_it(env):
    clock, provider, cache = env
    unmapped, stock = instrument("U"), instrument("S")
    provider.unmapped = {unmapped}
    provider.fail = True
    items = [unmapped, stock, instrument("S2"), instrument("S3")]
    cache.quotes(items)  # three real failures in one cycle
    calls = provider.calls
    assert "provider_backoff" in codes(cache.quote(instrument("T")).valuation)
    assert provider.calls == calls  # real outage still triggers provider-wide backoff
    provider.fail = False
    for _ in range(40):  # outage over; backoff is at most 300 s
        clock.advance(15)
        if cache.quotes(items)[stock].valuation.status is ValueStatus.AVAILABLE:
            break
    assert cache.quotes(items)[stock].valuation.status is ValueStatus.AVAILABLE


def test_success_resets_provider_failure_count(env):
    clock, provider, cache = env
    for fail in (True, True, False, True, True):
        provider.fail = fail
        clock.advance(300)
        cache.quote(instrument(), refresh=True)
    provider.fail = False
    calls = provider.calls
    cache.fx(PAIR)  # no provider-wide backoff: two failures after the reset
    assert provider.calls == calls + 1


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
    assert {"price_source_fallback", "sheet_value_quote_time_unknown", "provider_failure"} <= codes(
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
