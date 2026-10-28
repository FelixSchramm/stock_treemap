"""In-memory TTL cache with backoff around MarketDataProvider; no scheduler.

The clock is injected. The cache starts empty on every process start, so a
failure without a prior success stays unavailable and never invents a last
state. After a prior success, a failure returns that value relabeled
CACHED_PROVIDER with its original retrieval/observation times (the visible age).
A manual refresh (refresh=True) skips only the TTL; it obeys the same backoff.
quotes() bundles a cycle: only due keys are fetched, and after
provider_failure_threshold consecutive provider failures (warning code
provider_failure, including provider-rejected symbols) a provider-wide backoff
stops further fetches (quotes, FX, history; not holdings). Missing mappings and
validation failures do not count. Bundled quotes try keys with fewer failures
first, so permanently rejected symbols cannot keep taking the recovery probe.
A key already in its own failure streak is ignored only directly after a
healthy key succeeded without any new failure, so rejected symbols cannot block
FX, history or other keys while a partial recovery still rearms the breaker.
A failing listing is not yet a provider outage: quotes() accepts replacement
listings and tries them directly after the failed primary, so a healthy
replacement resets the failure streak before further primaries are probed. If
the replacements fail too, the provider-wide backoff opens as before.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Generic, TypeVar

from portfolio_dashboard.config import CacheTTLs
from portfolio_dashboard.market_data import (
    FxObservation,
    MarketDataProvider,
    PriceHistory,
    PriceObservation,
    VerifiedFxPair,
)
from portfolio_dashboard.models import (
    DataWarning,
    Instrument,
    PortfolioSnapshot,
    Provenance,
    SnapshotStatus,
    Valuation,
    ValuationSource,
    ValueStatus,
)

T = TypeVar("T")

_CACHED = DataWarning(
    "cached_provider_value",
    "Provider refresh failed; showing the last successful provider value",
)


_BACKOFF = DataWarning(
    "provider_backoff", "Provider backoff active; no fetch attempted"
)
_CACHED_HOLDINGS = DataWarning(
    "cached_holdings_snapshot",
    "Holdings refresh failed; showing the last successful snapshot",
)


@dataclass(slots=True)
class _Entry(Generic[T]):
    success: T | None = None
    fetched_at: datetime | None = None
    failures: int = 0
    retry_after: datetime | None = None
    last_failure: T | None = None


@dataclass(slots=True)
class QuoteCache:
    provider: MarketDataProvider
    ttls: CacheTTLs = field(default_factory=CacheTTLs)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    backoff_base_seconds: int = 15
    backoff_max_seconds: int = 300
    provider_failure_threshold: int = 3
    _entries: dict = field(default_factory=dict, init=False, repr=False)
    _provider_failures: int = field(default=0, init=False, repr=False)
    _provider_retry_after: datetime | None = field(default=None, init=False, repr=False)
    _healthy_success: bool = field(default=False, init=False, repr=False)

    def _get(
        self,
        key,
        ttl_seconds,
        fetch,
        is_ok,
        as_cached,
        refresh,
        unavailable,
        provider_wide=True,
    ):
        now = self.clock()
        entry = self._entries.setdefault(key, _Entry())
        if (
            entry.success is not None
            and not refresh
            and now - entry.fetched_at < timedelta(seconds=ttl_seconds)
        ):
            return as_cached(entry.success) if entry.failures else entry.success
        provider_blocked = (
            provider_wide
            and self._provider_retry_after is not None
            and now < self._provider_retry_after
        )
        if not provider_blocked and (
            entry.retry_after is None or now >= entry.retry_after
        ):
            fetched = fetch()
            if is_ok(fetched):
                was_healthy = entry.failures == 0
                self._entries[key] = _Entry(fetched, now)
                if provider_wide:
                    self._provider_failures = 0
                    self._provider_retry_after = None
                    self._healthy_success = was_healthy
                return fetched
            if (
                provider_wide
                and _is_provider_outage(fetched)
                and (
                    entry.failures == 0
                    or self._provider_failures > 0
                    or not self._healthy_success
                )
            ):
                self._provider_failures += 1
                self._healthy_success = False
                excess = self._provider_failures - self.provider_failure_threshold
                if excess >= 0:
                    self._provider_retry_after = now + timedelta(
                        seconds=min(
                            self.backoff_base_seconds * 2**excess,
                            self.backoff_max_seconds,
                        )
                    )
            entry.last_failure = fetched
            entry.failures += 1
            delay = min(
                self.backoff_base_seconds * 2 ** (entry.failures - 1),
                self.backoff_max_seconds,
            )
            entry.retry_after = now + timedelta(seconds=delay)
        if entry.success is not None:
            return as_cached(entry.success)
        if entry.last_failure is None:
            return unavailable(now)
        return entry.last_failure

    def quote(
        self, instrument: Instrument, *, refresh: bool = False
    ) -> PriceObservation:
        return self._get(
            ("quote", instrument),
            self.ttls.quotes_seconds,
            lambda: self.provider.latest_quote(instrument),
            lambda obs: obs.valuation.status is ValueStatus.AVAILABLE,
            lambda obs: replace(obs, valuation=_cached_valuation(obs.valuation)),
            refresh,
            lambda now: PriceObservation(
                Valuation(
                    None,
                    ValueStatus.MISSING,
                    _missing_provenance(instrument.provider_mapping, now),
                    warnings=(_BACKOFF,),
                )
            ),
        )

    def quotes(
        self,
        instruments: Iterable[Instrument],
        *,
        refresh: bool = False,
        replacements: Mapping[Instrument, Iterable[Instrument]] | None = None,
    ) -> dict[Instrument, PriceObservation]:
        """Fetch due keys with fewer failures first; return in input order.

        A replacement listing is fetched directly after its primary only if the
        primary quote is unavailable; replacements are appended after the inputs.
        """
        items = list(instruments)
        ordered = sorted(
            items, key=lambda item: self._entries.get(("quote", item), _Entry()).failures
        )
        results = {}
        for item in ordered:
            results[item] = self.quote(item, refresh=refresh)
            if results[item].valuation.status is ValueStatus.AVAILABLE:
                continue
            for replacement in (replacements or {}).get(item, ()):
                if replacement not in results and replacement not in items:
                    results[replacement] = self.quote(replacement, refresh=refresh)
        extras = {key: value for key, value in results.items() if key not in items}
        return {item: results[item] for item in items} | extras

    def fx(self, pair: VerifiedFxPair, *, refresh: bool = False) -> FxObservation:
        return self._get(
            ("fx", pair),
            self.ttls.quotes_seconds,
            lambda: self.provider.fx(pair),
            lambda obs: obs.status is ValueStatus.AVAILABLE,
            lambda obs: replace(
                obs,
                provenance=_cached_provenance(obs.provenance),
                warnings=obs.warnings + (_CACHED,),
            ),
            refresh,
            lambda now: FxObservation(
                pair,
                None,
                ValueStatus.MISSING,
                _missing_provenance(pair.mapping, now),
                (_BACKOFF,),
            ),
        )

    def history(
        self,
        instrument: Instrument,
        start: date,
        end: date,
        *,
        minimum_points: int = 2,
        refresh: bool = False,
    ) -> PriceHistory:
        """Cached for the history TTL; callers pass only closed periods (current day: #22)."""
        return self._get(
            ("history", instrument, start, end, minimum_points),
            self.ttls.history_seconds,
            lambda: self.provider.history(
                instrument, start, end, minimum_points=minimum_points
            ),
            lambda history: history.status is ValueStatus.AVAILABLE,
            lambda history: replace(
                history,
                provenance=_cached_provenance(history.provenance),
                warnings=history.warnings + (_CACHED,),
            ),
            refresh,
            lambda now: PriceHistory(
                ValueStatus.MISSING,
                _missing_provenance(instrument.provider_mapping, now),
                warnings=(_BACKOFF,),
            ),
        )

    def holdings(
        self, load: Callable[[], PortfolioSnapshot], *, refresh: bool = False
    ) -> PortfolioSnapshot:
        """Keep the last non-FAILED snapshot; its attempted_at shows the age.

        After a failed refresh it is returned with the cached_holdings_snapshot warning.
        """
        return self._get(
            ("holdings",),
            self.ttls.holdings_seconds,
            load,
            lambda snapshot: snapshot.status is not SnapshotStatus.FAILED,
            lambda snapshot: replace(
                snapshot, warnings=snapshot.warnings + (_CACHED_HOLDINGS,)
            ),
            refresh,
            None,
            provider_wide=False,
        )


def _is_provider_outage(result) -> bool:
    """Count provider_failure, which can also mean the provider rejected a symbol."""
    warnings = getattr(getattr(result, "valuation", result), "warnings", ())
    return any(warning.code == "provider_failure" for warning in warnings)


def _missing_provenance(mapping, now: datetime) -> Provenance:
    provider = mapping.provider if mapping is not None else "unknown"
    return Provenance(ValuationSource.PROVIDER, now, provider=provider)


def _cached_provenance(provenance):
    return replace(provenance, source=ValuationSource.CACHED_PROVIDER)


def _cached_valuation(valuation: Valuation) -> Valuation:
    return replace(
        valuation,
        provenance=_cached_provenance(valuation.provenance),
        warnings=valuation.warnings + (_CACHED,),
    )


def choose_valuation(market: Valuation, sheet: Valuation | None) -> Valuation:
    """Verified provider price, else cached provider price, else flagged EUR Sheet value.

    The cache already yields PROVIDER or CACHED_PROVIDER (with age). Only a
    Sheet value in EUR may replace an unavailable provider price; the switch is
    visible as source and warning, and an unknown quote time is stated.
    """
    if market.status is ValueStatus.AVAILABLE:
        return market
    if (
        sheet is None
        or sheet.status is not ValueStatus.AVAILABLE
        or sheet.provenance.source is not ValuationSource.SHEET
        or sheet.unit is None
        or (sheet.unit.currency, sheet.unit.scale_to_currency) != ("EUR", 1)
    ):
        return market
    notes = [
        DataWarning(
            "price_source_fallback", "Provider price unavailable; using Sheet value"
        )
    ]
    if sheet.provenance.observed_at is None:
        notes.append(
            DataWarning(
                "sheet_value_quote_time_unknown", "Sheet-Wert, Kursstand unbekannt"
            )
        )
    return replace(sheet, warnings=sheet.warnings + market.warnings + tuple(notes))


def supports_period_return(valuation: Valuation) -> bool:
    """A Sheet/manual price has no consistent history, so it yields no period return."""
    return (
        valuation.status is ValueStatus.AVAILABLE
        and valuation.provenance.source is not ValuationSource.SHEET
    )
