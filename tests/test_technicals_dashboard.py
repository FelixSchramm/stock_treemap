"""Technical signals in the refresh bundle, the positions table and the demo (#144)."""

import os
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from streamlit.dataframe_util import convert_arrow_bytes_to_pandas_df
from streamlit.testing.v1 import AppTest

from portfolio_dashboard.config import CacheTTLs
from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.market_data import (
    AdjustmentBasis,
    HistoryPoint,
    PriceHistory,
    PriceObservation,
)
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    PortfolioSnapshot,
    Position,
    Provenance,
    ProviderMapping,
    QuoteUnit,
    SnapshotStatus,
    Valuation,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.price_periods import session_calendar
from portfolio_dashboard.quote_cache import QuoteCache
from portfolio_dashboard.refresh import PortfolioRefreshCoordinator
from portfolio_dashboard.technicals import (
    SupportCandidate,
    SupportMethod,
    SupportZone,
    TechnicalSignals,
    Trend,
    unavailable_signals,
)
from portfolio_dashboard.treemap import (
    NOT_AVAILABLE,
    SORT_FIELDS,
    TECHNICAL_FIELDS,
    TECHNICALS_NOTE,
    Performance,
    build_comparison_table_rows,
    sort_table_rows,
)
from portfolio_dashboard.ui import COURSE_PERIODS, PERIOD_RADIO_KEY, SORT_FIELD_KEY
from portfolio_dashboard.valuations import value_portfolio

EUR = QuoteUnit("EUR", Decimal("1"))
T0 = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)  # Friday, 12:00 in Frankfurt
APP_PATH = Path(__file__).parents[1] / "app.py"


def _weekdays(first: date, last: date) -> list[date]:
    days, day = [], first
    while day <= last:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


SESSIONS = _weekdays(date(2025, 9, 1), date(2026, 10, 23))
CALENDAR = session_calendar(SESSIONS, time(9, 0), time(17, 30), SESSIONS[0], SESSIONS[-1])


class _Provider:
    """Offline provider: rising synthetic closes, one live quote at T0."""

    def __init__(self) -> None:
        self.history_calls = 0

    def latest_quote(self, instrument):
        prov = Provenance(ValuationSource.PROVIDER, T0, observed_at=T0, provider="yfinance")
        return PriceObservation(
            Valuation(Decimal("140"), ValueStatus.AVAILABLE, prov, EUR),
            AdjustmentBasis.YAHOO_SPLIT_ADJUSTED_PRICE,
        )

    def history(self, instrument, start, end, *, minimum_points=2):
        self.history_calls += 1
        prov = Provenance(ValuationSource.PROVIDER, T0, observed_at=T0, provider="yfinance")
        points = tuple(
            HistoryPoint(day, Decimal(80) + Decimal("0.2") * index, Decimal(0))
            for index, day in enumerate(SESSIONS[:-1])
        )
        return PriceHistory(
            ValueStatus.AVAILABLE, prov, EUR, "Europe/Berlin",
            AdjustmentBasis.YAHOO_SPLIT_ADJUSTED_PRICE, points,
        )


class _CountingCache(QuoteCache):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.history_lookups = 0

    def history(self, *args, **kwargs):
        self.history_lookups += 1
        return super().history(*args, **kwargs)


def _position(row, name, instrument, price="100"):
    return Position(
        name=name, quantity=Decimal(3), instrument=instrument, category="Synthetik",
        source_row=row, imported_at=T0,
        valuation=Valuation(Decimal(price), ValueStatus.AVAILABLE,
                            Provenance(ValuationSource.SHEET, T0), EUR),
    )


def _coordinator():
    identity = Instrument(InstrumentType.STOCK, instrument_id="SYN-1",
                          trading_venue="FRA", quote_unit=EUR)
    listing = VerifiedListing(
        instrument=Instrument(InstrumentType.STOCK, instrument_id="SYN-1",
                              trading_venue="FRA", quote_unit=EUR,
                              provider_mapping=ProviderMapping("yfinance", "SYN1.F")),
        equity_form=EquityForm.SHARE,
    )
    unmapped = Instrument(InstrumentType.ETF, display_ticker="OHNE")
    snapshot = PortfolioSnapshot(T0, SnapshotStatus.COMPLETE, (
        _position(1, "Synthetik Aktie", identity),
        _position(2, "Ohne Zuordnung", unmapped),
    ))
    provider = _Provider()
    cache = _CountingCache(
        provider, ttls=CacheTTLs(quotes_seconds=300, history_seconds=3600,
                                 holdings_seconds=300),
        clock=lambda: T0,
    )
    coordinator = PortfolioRefreshCoordinator(
        lambda _: snapshot, cache, listings=(listing,),
        calendars={"FRA": CALENDAR}, clock=lambda: T0,
    )
    return coordinator, cache, provider


def test_bundle_carries_signals_from_the_loaded_history_without_extra_lookups():
    coordinator, cache, provider = _coordinator()
    bundle = coordinator.load(refresh=True)
    assert bundle.available
    assert len(bundle.technical_signals) == len(bundle.valuation.groups) == 2
    by_name = {
        group.members[0].position.name: signals
        for group, signals in zip(bundle.valuation.groups, bundle.technical_signals)
    }
    mapped = by_name["Synthetik Aktie"]
    # The series ends with the "Heute" price: the live quote during the session.
    assert mapped.current_price == Decimal("140")
    assert mapped.currency == "EUR"
    assert mapped.trend is Trend.UP
    assert mapped.next_support is not None
    # One history lookup and one provider call for the one mapped listing:
    # the technical signals reuse the history of the course periods.
    assert cache.history_lookups == 1
    assert provider.history_calls == 1
    unmapped = by_name["Ohne Zuordnung"]
    assert unmapped.trend is None
    assert unmapped.trend_reason == "unverified_instrument"
    assert unmapped.next_support is None


def _signals(trend, support=None, strength=1, rsi=None, high=None):
    zone = None
    if support is not None:
        methods = list(SupportMethod)[:strength]
        zone = SupportZone(Decimal(support), tuple(
            SupportCandidate(Decimal(support), method, method.value) for method in methods
        ))
    return TechnicalSignals(
        current_price=Decimal(100), currency="USD", trend=trend,
        zones=(zone,) if zone else (), next_support=zone,
        support_reason=None if zone else "no_support_below",
        rsi=None if rsi is None else Decimal(rsi), rsi_reason=None if rsi else "insufficient_history",
        high_distance_percent=None if high is None else Decimal(high),
    )


def _rows(signals):
    names = ("Alpha", "Beta", "Gamma", "Delta")[: len(signals)]
    snapshot = PortfolioSnapshot(T0, SnapshotStatus.COMPLETE, tuple(
        _position(i, name, Instrument(InstrumentType.STOCK, display_ticker=name.upper()))
        for i, name in enumerate(names, 1)
    ))
    valuation = value_portfolio(snapshot, ())
    missing = (Performance(None),) * len(names)
    return build_comparison_table_rows(
        valuation, dict.fromkeys(COURSE_PERIODS, missing), technical_signals=signals,
    )


SIGNALS = (
    _signals(Trend.BOTTOMING, "95", strength=3, rsi="28.5", high="-20"),
    _signals(Trend.UP, "99", strength=1, rsi="65", high="-1.5"),
    unavailable_signals("unverified_instrument"),
    _signals(Trend.DOWN, None, rsi="45", high="-40"),
)


def test_technical_columns_follow_heute_and_stay_numeric():
    rows = _rows(SIGNALS)
    display = rows[0].to_display_dict()
    columns = list(display)
    start = columns.index("Heute (%)") + 1
    assert columns[start:start + len(TECHNICAL_FIELDS)] == list(TECHNICAL_FIELDS)
    assert display["Trend"] == 2.0  # rank; the label is display formatting only
    assert display["Nächste Unterstützung"] == 95.0
    assert display["Abstand Unterstützung (%)"] == -5.0
    assert display["Stärke"] == 3.0
    assert display["RSI (14)"] == 28.5
    assert display["Abstand 52W-Hoch (%)"] == -20.0
    unavailable = rows[2].to_display_dict()
    assert all(unavailable[field] is None for field in TECHNICAL_FIELDS)


def test_all_technical_columns_are_sortable_with_missing_values_last():
    rows = _rows(SIGNALS)
    assert set(TECHNICAL_FIELDS) <= set(SORT_FIELDS)

    def order(field):
        return [row.name for row in sort_table_rows(rows, sort_by=field)]

    assert order("Trend") == ["Beta", "Alpha", "Delta", "Gamma"]
    assert order("Nächste Unterstützung") == ["Beta", "Alpha", "Delta", "Gamma"]
    assert order("Abstand Unterstützung (%)") == ["Beta", "Alpha", "Delta", "Gamma"]
    assert order("Stärke") == ["Alpha", "Beta", "Delta", "Gamma"]
    assert order("RSI (14)") == ["Beta", "Delta", "Alpha", "Gamma"]
    assert order("Abstand 52W-Hoch (%)") == ["Beta", "Alpha", "Delta", "Gamma"]
    ascending = [row.name for row in sort_table_rows(rows, sort_by="RSI (14)", descending=False)]
    assert ascending == ["Alpha", "Delta", "Beta", "Gamma"]


def test_details_list_zones_rule_crossing_and_reasons_without_hover():
    rows = _rows(SIGNALS)
    alpha = " · ".join(rows[0].detail_lines())
    assert "Trend: Bodenbildung (Regel: Kurs < SMA200" in alpha
    assert "Unterstützungszonen: 95,00 USD (Stärke 3: Swing-Tief, Gleitender Durchschnitt, Fibonacci)" in alpha
    assert "Nächste Unterstützung: 95,00 USD (-5,00 %, Stärke 3)" in alpha
    assert "RSI (14): 28,50 (überverkauft)" in alpha
    assert "Abstand 52W-Hoch: -20,00 %" in alpha
    assert "Kreuzungssignal: keines" in alpha
    gamma = " · ".join(rows[2].detail_lines())
    assert f"Trend: {NOT_AVAILABLE} (keine verifizierte Kurszuordnung)" in gamma
    delta = " · ".join(rows[3].detail_lines())
    assert f"Nächste Unterstützung: {NOT_AVAILABLE} (keine Unterstützung unter dem Kurs)" in delta


def test_demo_table_shows_formatted_technical_columns_and_the_note(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("PORTFOLIO_"):
            monkeypatch.delenv(name)
    app = AppTest.from_file(str(APP_PATH)).run()
    assert not app.exception
    table = app.dataframe[0].value
    columns = table.columns.tolist()
    start = columns.index("Heute (%)") + 1
    assert columns[start:start + len(TECHNICAL_FIELDS)] == list(TECHNICAL_FIELDS)
    displayed = convert_arrow_bytes_to_pandas_df(
        app.dataframe[0].proto.arrow_data.styler.display_values
    )
    assert displayed["Trend"].tolist() == ["Aufwärtstrend", "Bodenbildung"]
    assert displayed["Nächste Unterstützung"].tolist() == ["113,90 EUR", "190,45 EUR"]
    assert displayed["Abstand Unterstützung (%)"].tolist() == ["-5,08 %", "-4,78 %"]
    assert displayed["Stärke"].tolist() == ["2", "2"]
    assert displayed["RSI (14)"].tolist() == ["61,35 (neutral)", "27,80 (überverkauft)"]
    assert TECHNICALS_NOTE in [caption.value for caption in app.caption]
    details = [block.value for block in app.markdown if "Unterstützungszonen" in block.value]
    assert len(details) == 2
    assert "Death Cross" in details[1]

    # The period selection of the treemap does not change the new columns.
    for period in ("Jahr", "Seit Kauf"):
        app.segmented_control(key=PERIOD_RADIO_KEY).set_value(period).run()
        assert not app.exception
        current = app.dataframe[0].value
        assert current[list(TECHNICAL_FIELDS)].equals(table[list(TECHNICAL_FIELDS)])

    app.selectbox(key=SORT_FIELD_KEY).set_value("RSI (14)").run()
    assert app.dataframe[0].value["Instrument"].tolist() == ["Demo-Aktie A", "Demo-Fonds B"]
