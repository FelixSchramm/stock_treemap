"""Atomic refresh coordinator for holdings, quotes, FX and period histories (M5-04).

Connects read-only Sheet imports and cached market data into a single atomic
``DashboardBundle``. If a refresh fails after a prior success, the complete
previous bundle is retained with its age so mixed half-finished states never
appear. Without a prior valid state, failures report "Daten nicht verfügbar".
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from portfolio_dashboard.instruments import VerifiedListing, resolve_listing
from portfolio_dashboard.market_data import PriceObservation, VerifiedFxPair
from portfolio_dashboard.models import (
    DataWarning,
    Instrument,
    PortfolioSnapshot,
    Position,
    PositionActivity,
    SnapshotStatus,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.price_periods import (
    PricePeriod,
    SessionCalendar,
    price_period_returns,
)
from portfolio_dashboard.quote_cache import (
    QuoteCache,
    choose_valuation,
    supports_period_return,
)
from portfolio_dashboard.returns import PortfolioReturns, portfolio_returns
from portfolio_dashboard.sheets_adapter import SheetsReadResult
from portfolio_dashboard.treemap import (
     SHEET_UNKNOWN_QUOTE_STATUS,
    Performance,
    performance_from_period,
)
from portfolio_dashboard.valuations import (
    FxRate,
    PortfolioValuation,
    ValuationGroup,
    value_portfolio,
)

PERIOD_KEY_TO_ENUM = {
    "Heute": PricePeriod.TODAY,
    "Woche": PricePeriod.WEEK,
    "Monat": PricePeriod.MONTH,
    "Jahr": PricePeriod.YEAR,
}
DATA_UNAVAILABLE_TEXT = "Daten nicht verfügbar"


def format_snapshot_age(attempted_at: datetime, now: datetime) -> str:
    """Format the elapsed time between a snapshot timestamp and ``now`` in German.

    :param attempted_at: Timezone-aware timestamp of the snapshot.
    :param now: Timezone-aware reference clock time.
    :return: Human-readable German age string (e.g. ``"vor 5 Min."``).
    """
    delta_seconds = max(0, int((now - attempted_at).total_seconds()))
    if delta_seconds < 60:
        return f"vor {delta_seconds} Sek."
    minutes = delta_seconds // 60
    if minutes < 60:
        return f"vor {minutes} Min."
    hours, rem_minutes = divmod(minutes, 60)
    if hours < 24:
        if rem_minutes:
            return f"vor {hours} Std. {rem_minutes} Min."
        return f"vor {hours} Std."
    days = hours // 24
    return f"vor {days} Tag(en)"


def _format_timestamp(stamp: datetime) -> str:
    """Format an aware timestamp in standard German dashboard notation.

    :param stamp: Timezone-aware datetime.
    :return: Formatted timestamp string.
    """
    return stamp.strftime("%d.%m.%Y, %H:%M %Z")


@dataclass(frozen=True, slots=True)
class DashboardBundle:
    """Self-consistent, atomically swapped state for the dashboard UI."""

    available: bool
    snapshot: PortfolioSnapshot
    valuation: PortfolioValuation
    returns: PortfolioReturns
    course_performances: Mapping[str, tuple[Performance, ...]]
    period_reasons: Mapping[str, tuple[str | None, ...]]
    last_attempt_at: datetime
    using_cached_stand: bool = False
    status_message: str | None = None

    def performances_for(self, view: str, period: str) -> tuple[Performance, ...]:
        """Return group-aligned performances for the active view and period.

        :param view: Active view name (course or personal view).
        :param period: Active course period name.
        :return: Tuple of Performance objects aligned with ``valuation.groups``.
        """
        if view == "Meine Entwicklung":
            return tuple(
                Performance(group.return_percent) for group in self.returns.groups
            )
        return self.course_performances.get(
            period, (Performance(None),) * len(self.valuation.groups)
        )

    def reasons_for(self, view: str, period: str) -> tuple[str | None, ...]:
        """Return group-aligned period reason codes for the active view and period.

        :param view: Active view name.
        :param period: Active course period name.
        :return: Tuple of optional reason codes aligned with ``valuation.groups``.
        """
        if view == "Meine Entwicklung":
            return (None,) * len(self.valuation.groups)
        return self.period_reasons.get(
            period, (None,) * len(self.valuation.groups)
        )

    def age_label(self, now: datetime | None = None) -> str:
        """Build a German timestamp and age description for the active snapshot.

        :param now: Optional current clock timestamp; defaults to ``last_attempt_at``.
        :return: Formatted timestamp and age string.
        """
        ref = now if now is not None else self.last_attempt_at
        stamp_text = _format_timestamp(self.snapshot.attempted_at)
        age_text = format_snapshot_age(self.snapshot.attempted_at, ref)
        return f"{stamp_text} (Alter: {age_text})"


def _ensure_sheet_unknown_warning(position: Position) -> Position:
    """Ensure manual Sheet valuations without quote time carry the required warning.

    :param position: Imported Position to inspect.
    :return: Position with ``sheet_value_quote_time_unknown`` warning if applicable.
    """
    quote = position.valuation
    if (
        quote is None
        or quote.status is not ValueStatus.AVAILABLE
        or quote.provenance.source is not ValuationSource.SHEET
        or quote.provenance.observed_at is not None
    ):
        return position
    if any(w.code == "sheet_value_quote_time_unknown" for w in quote.warnings):
        return position
    warning = DataWarning(
        "sheet_value_quote_time_unknown", SHEET_UNKNOWN_QUOTE_STATUS
    )
    return replace(
        position,
        valuation=replace(quote, warnings=quote.warnings + (warning,)),
    )


def _group_period_results(
    group: ValuationGroup,
    listings: tuple[VerifiedListing, ...],
    quote_map: Mapping[Instrument, PriceObservation],
    calendars: Mapping[str, SessionCalendar],
    cache: QuoteCache,
    now: datetime,
    *,
    refresh: bool,
) -> tuple[dict[str, Performance], dict[str, str | None]]:
    """Compute course performances and reason codes across all four periods for a group.

    :param group: Valuation group to evaluate.
    :param listings: Verified instrument listings.
    :param quote_map: Bundled quote observations keyed by listing instrument.
    :param calendars: Exchange session calendars keyed by trading venue or ``"DEFAULT"``.
    :param cache: QuoteCache used for history lookups.
    :param now: Aware evaluation timestamp.
    :param refresh: Whether to bypass history cache TTL.
    :return: Tuple of (period->Performance dict, period->reason dict).
    """
    active = [
        m
        for m in group.members
        if m.position.activity is PositionActivity.ACTIVE
    ]
    if not active:
        return (
            {p: Performance(None) for p in PERIOD_KEY_TO_ENUM},
            {p: "inactive" for p in PERIOD_KEY_TO_ENUM},
        )

    first = active[0].position
    listing = resolve_listing(first.instrument, listings)
    valued = [m for m in active if m.amount_eur is not None]

    if not valued:
        return (
            {p: Performance(None) for p in PERIOD_KEY_TO_ENUM},
            {p: "unavailable_value" for p in PERIOD_KEY_TO_ENUM},
        )

    if any(
        m.position.valuation is None
        or not supports_period_return(m.position.valuation)
        for m in valued
    ):
        return (
            {p: Performance(None) for p in PERIOD_KEY_TO_ENUM},
            {p: "sheet_fallback" for p in PERIOD_KEY_TO_ENUM},
        )

    if listing is None or listing.instrument.provider_mapping is None:
        return (
            {p: Performance(None) for p in PERIOD_KEY_TO_ENUM},
            {p: "unverified_instrument" for p in PERIOD_KEY_TO_ENUM},
        )

    venue = listing.instrument.trading_venue or "DEFAULT"
    calendar = calendars.get(venue) or calendars.get("DEFAULT")
    if calendar is None:
        return (
            {p: Performance(None) for p in PERIOD_KEY_TO_ENUM},
            {p: "calendar_incomplete" for p in PERIOD_KEY_TO_ENUM},
        )

    history = cache.history(
        listing.instrument,
        calendar.first_day,
        calendar.last_day,
        refresh=refresh,
    )
    quote_obs = quote_map.get(listing.instrument)
    results = price_period_returns(history, quote_obs, calendar, now)

    perfs: dict[str, Performance] = {}
    reasons: dict[str, str | None] = {}
    for label, period_enum in PERIOD_KEY_TO_ENUM.items():
        res = results[period_enum]
        perfs[label] = performance_from_period(res)
        reasons[label] = res.reason
    return perfs, reasons


class PortfolioRefreshCoordinator:
    """Coordinate atomic holdings and market-data refreshes with fallback retention."""

    def __init__(
        self,
        read_holdings: Callable[[datetime], SheetsReadResult | PortfolioSnapshot],
        cache: QuoteCache,
        *,
        listings: tuple[VerifiedListing, ...] = (),
        fx_pairs: Mapping[str, VerifiedFxPair] | None = None,
        calendars: Mapping[str, SessionCalendar] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Initialize the refresh coordinator.

        :param read_holdings: Read-only callable taking ``attempted_at`` and returning
            a ``SheetsReadResult`` or ``PortfolioSnapshot``.
        :param cache: ``QuoteCache`` instance managing TTL and provider backoff.
        :param listings: Verified instrument listings registry.
        :param fx_pairs: Optional mapping of foreign currency code to ``VerifiedFxPair``.
        :param calendars: Optional mapping of trading venue to ``SessionCalendar``.
        :param clock: Injectable timezone-aware clock callable.
        """
        self._read_holdings = read_holdings
        self._cache = cache
        self._listings = listings
        self._fx_pairs = dict(fx_pairs) if fx_pairs is not None else {}
        self._calendars = dict(calendars) if calendars is not None else {}
        self._clock = clock
        self._last_bundle: DashboardBundle | None = None

    @property
    def last_bundle(self) -> DashboardBundle | None:
        """Return the last successfully assembled atomic bundle, if any.

        :return: Last valid ``DashboardBundle`` or ``None``.
        """
        return self._last_bundle

    def _fetch_snapshot(self, attempted_at: datetime) -> PortfolioSnapshot:
        """Invoke the read-only holdings loader and unwrap ``SheetsReadResult``.

        :param attempted_at: Aware attempt timestamp.
        :return: Imported ``PortfolioSnapshot``.
        """
        try:
            try:
                result = self._read_holdings(attempted_at=attempted_at)
            except TypeError:
                result = self._read_holdings(attempted_at)
        except Exception:
            return PortfolioSnapshot(
                attempted_at,
                SnapshotStatus.FAILED,
                warnings=(
                    DataWarning(
                        "sheet_transport_error",
                        "Sheet read could not be completed.",
                    ),
                ),
            )
        if isinstance(result, SheetsReadResult):
            return result.snapshot
        return result

    def _fallback_or_unavailable(
        self, now: datetime, failed_snapshot: PortfolioSnapshot | None = None
    ) -> DashboardBundle:
        """Return the atomic previous bundle with age or an unavailable bundle.

        :param now: Current aware timestamp of the failed attempt.
        :param failed_snapshot: Optional failed snapshot carrying error warnings.
        :return: Preserved previous ``DashboardBundle`` or unavailable bundle.
        """
        if self._last_bundle is not None:
            age = format_snapshot_age(self._last_bundle.snapshot.attempted_at, now)
            stamp = _format_timestamp(self._last_bundle.snapshot.attempted_at)
            msg = (
                f"Aktualisierung fehlgeschlagen – vorhandener Stand vom {stamp} "
                f"(Alter: {age}) bleibt unverändert erhalten."
            )
            return replace(
                self._last_bundle,
                last_attempt_at=now,
                using_cached_stand=True,
                status_message=msg,
            )

        snap = (
            failed_snapshot
            if failed_snapshot is not None
            else PortfolioSnapshot(
                now,
                SnapshotStatus.FAILED,
                warnings=(
                    DataWarning(
                        "data_unavailable",
                        "Kein gültiger Datenstand verfügbar.",
                    ),
                ),
            )
        )
        empty_val = value_portfolio(snap, self._listings)
        empty_ret = portfolio_returns(empty_val)
        return DashboardBundle(
            available=False,
            snapshot=snap,
            valuation=empty_val,
            returns=empty_ret,
            course_performances={p: () for p in PERIOD_KEY_TO_ENUM},
            period_reasons={p: () for p in PERIOD_KEY_TO_ENUM},
            last_attempt_at=now,
            using_cached_stand=False,
            status_message=(
                f"{DATA_UNAVAILABLE_TEXT}: Bestandsdaten konnten nicht geladen werden "
                "und es liegt kein vorheriger Stand vor."
            ),
        )

    def load(self, *, refresh: bool = False) -> DashboardBundle:
        """Load or refresh holdings and market data, swapping the bundle atomically.

        :param refresh: If True, bypass cache TTLs while respecting provider backoff.
        :return: Atomically consistent ``DashboardBundle``.
        """
        now = self._clock()
        try:
            raw_snapshot = self._cache.holdings(
                lambda: self._fetch_snapshot(now), refresh=refresh
            )
        except Exception:
            return self._fallback_or_unavailable(now)

        if raw_snapshot is None or raw_snapshot.status is SnapshotStatus.FAILED:
            return self._fallback_or_unavailable(now, raw_snapshot)

        if (
            any(w.code == "cached_holdings_snapshot" for w in raw_snapshot.warnings)
            and self._last_bundle is not None
        ):
            return self._fallback_or_unavailable(now, raw_snapshot)

        try:
            mapped_instruments: list[Instrument] = []
            seen_instruments: set[Instrument] = set()
            for pos in raw_snapshot.positions:
                if pos.activity is not PositionActivity.ACTIVE:
                    continue
                listing = resolve_listing(pos.instrument, self._listings)
                if (
                    listing is not None
                    and listing.instrument.provider_mapping is not None
                    and listing.instrument not in seen_instruments
                ):
                    seen_instruments.add(listing.instrument)
                    mapped_instruments.append(listing.instrument)

            quote_map = self._cache.quotes(mapped_instruments, refresh=refresh)

            enriched_positions: list[Position] = []
            needed_currencies: set[str] = set()
            for pos in raw_snapshot.positions:
                if pos.activity is not PositionActivity.ACTIVE:
                    enriched_positions.append(pos)
                    continue
                listing = resolve_listing(pos.instrument, self._listings)
                if (
                    listing is not None
                    and listing.instrument.provider_mapping is not None
                ):
                    obs = quote_map[listing.instrument]
                    chosen = choose_valuation(obs.valuation, pos.valuation)
                    updated_inst = (
                        listing.instrument
                        if chosen.provenance.source is not ValuationSource.SHEET
                        else pos.instrument
                    )
                    updated_pos = replace(
                        pos, instrument=updated_inst, valuation=chosen
                    )
                else:
                    updated_pos = _ensure_sheet_unknown_warning(pos)

                enriched_positions.append(updated_pos)
                if (
                    updated_pos.valuation is not None
                    and updated_pos.valuation.status is ValueStatus.AVAILABLE
                    and updated_pos.valuation.unit is not None
                    and updated_pos.valuation.unit.currency != "EUR"
                ):
                    needed_currencies.add(updated_pos.valuation.unit.currency)

            fx_rates: dict[str, FxRate] = {}
            for currency in sorted(needed_currencies):
                pair = self._fx_pairs.get(currency)
                if pair is None:
                    continue
                fx_obs = self._cache.fx(pair, refresh=refresh)
                if (
                    fx_obs.status is ValueStatus.AVAILABLE
                    and fx_obs.rate is not None
                    and fx_obs.rate > Decimal(0)
                ):
                    fx_rates[currency] = FxRate(
                        pair.base_unit.currency,
                        pair.quote_unit.currency,
                        fx_obs.rate,
                        fx_obs.provenance,
                        fx_obs.warnings,
                    )

            enriched_snapshot = replace(
                raw_snapshot, positions=tuple(enriched_positions)
            )
            valuation = value_portfolio(
                enriched_snapshot, self._listings, fx_rates
            )
            returns = portfolio_returns(valuation)

            course_perfs: dict[str, list[Performance]] = {
                p: [] for p in PERIOD_KEY_TO_ENUM
            }
            period_reasons: dict[str, list[str | None]] = {
                p: [] for p in PERIOD_KEY_TO_ENUM
            }

            for group in valuation.groups:
                g_perfs, g_reasons = _group_period_results(
                    group,
                    self._listings,
                    quote_map,
                    self._calendars,
                    self._cache,
                    now,
                    refresh=refresh,
                )
                for label in PERIOD_KEY_TO_ENUM:
                    course_perfs[label].append(g_perfs[label])
                    period_reasons[label].append(g_reasons[label])

            status_msg = None
            if refresh:
                status_msg = (
                    f"Daten erfolgreich aktualisiert ({_format_timestamp(now)})."
                )

            bundle = DashboardBundle(
                available=True,
                snapshot=enriched_snapshot,
                valuation=valuation,
                returns=returns,
                course_performances={
                    k: tuple(v) for k, v in course_perfs.items()
                },
                period_reasons={
                    k: tuple(v) for k, v in period_reasons.items()
                },
                last_attempt_at=now,
                using_cached_stand=False,
                status_message=status_msg,
            )
        except Exception:
            return self._fallback_or_unavailable(now)

        self._last_bundle = bundle
        return bundle
