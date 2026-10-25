"""Offline tests for atomic refresh, read-only Sheet updates and failure states (M5-04)."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_dashboard.config import CacheTTLs, SourceConfig, SourceKind
from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.market_data import (
    AdjustmentBasis,
    FxObservation,
    HistoryPoint,
    PriceHistory,
    PriceObservation,
    VerifiedFxPair,
)
from portfolio_dashboard.models import (
    CostBasis,
    CostBasisStatus,
    DataWarning,
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
from portfolio_dashboard.price_periods import SessionCalendar, session_calendar
from portfolio_dashboard.quote_cache import QuoteCache
from portfolio_dashboard.refresh import (
    DATA_UNAVAILABLE_TEXT,
    PortfolioRefreshCoordinator,
    format_snapshot_age,
)
from portfolio_dashboard.sheet_parser import (
    SHEET_HEADERS,
    NumberLocale,
    SheetMetadata,
)
from portfolio_dashboard.sheets_adapter import SheetsAdapter
from portfolio_dashboard.treemap import (
    SHEET_UNKNOWN_QUOTE_STATUS,
    Performance,
    build_kpis,
    build_position_details,
    build_status_notes,
    build_table_rows,
)
from portfolio_dashboard.ui import REFRESH_BUTTON_KEY, render_dashboard
from portfolio_dashboard.valuations import value_portfolio

EUR = QuoteUnit("EUR", Decimal("1"))
USD = QuoteUnit("USD", Decimal("1"))
T0 = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)


class _ReadOnlySheetResponse:
    """Synthetic HTTP response for read-only Sheets GET requests."""

    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.closed = False

    def json(self, **kwargs):
        return self._payload

    def close(self) -> None:
        self.closed = True


class _ReadOnlySheetSession:
    """Synthetic Sheets session that only permits GET and records calls."""

    def __init__(self, responses: list[_ReadOnlySheetResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, str],
        timeout: float,
        allow_redirects: bool,
        verify: bool,
    ) -> _ReadOnlySheetResponse:
        self.calls.append(
            {
                "method": "GET",
                "url": url,
                "params": dict(params),
            }
        )
        return self.responses.pop(0)


class _FakeMarketProvider:
    """Deterministic offline market-data provider for refresh tests."""

    def __init__(
        self,
        *,
        quote_price: Decimal = Decimal("100"),
        observed_at: datetime = T0,
        fail_quotes: bool = False,
        history_bars: tuple[HistoryPoint, ...] = (),
    ) -> None:
        self.quote_price = quote_price
        self.observed_at = observed_at
        self.fail_quotes = fail_quotes
        self.history_bars = history_bars
        self.quote_calls = 0

    def latest_quote(self, instrument: Instrument) -> PriceObservation:
        self.quote_calls += 1
        prov = Provenance(
            ValuationSource.PROVIDER,
            self.observed_at,
            observed_at=self.observed_at,
            provider="yfinance",
        )
        if self.fail_quotes:
            return PriceObservation(
                Valuation(None, ValueStatus.MISSING, prov),
                AdjustmentBasis.YAHOO_SPLIT_ADJUSTED_PRICE,
            )
        return PriceObservation(
            Valuation(
                self.quote_price,
                ValueStatus.AVAILABLE,
                prov,
                instrument.quote_unit or EUR,
            ),
            AdjustmentBasis.YAHOO_SPLIT_ADJUSTED_PRICE,
        )

    def fx(self, pair: VerifiedFxPair) -> FxObservation:
        prov = Provenance(
            ValuationSource.PROVIDER,
            self.observed_at,
            observed_at=self.observed_at,
            provider="yfinance",
        )
        return FxObservation(pair, Decimal("0.90"), ValueStatus.AVAILABLE, prov)

    def history(
        self,
        instrument: Instrument,
        start: date,
        end: date,
        *,
        minimum_points: int = 2,
    ) -> PriceHistory:
        prov = Provenance(
            ValuationSource.PROVIDER,
            self.observed_at,
            observed_at=self.observed_at,
            provider="yfinance",
        )
        if len(self.history_bars) < minimum_points:
            return PriceHistory(ValueStatus.MISSING, prov)
        return PriceHistory(
            ValueStatus.AVAILABLE,
            prov,
            points=self.history_bars,
            unit=instrument.quote_unit or EUR,
            exchange_timezone="Europe/Berlin",
            adjustment_basis=AdjustmentBasis.YAHOO_SPLIT_ADJUSTED_PRICE,
        )


def _sheet_metadata() -> SheetMetadata:
    return SheetMetadata(
        number_locale=NumberLocale.GERMAN,
        cost_basis_verified_remaining=True,
    )


def _sheet_payload(rows: list[list]) -> dict:
    return {"majorDimension": "ROWS", "values": [list(SHEET_HEADERS), *rows]}


def _sample_calendar() -> SessionCalendar:
    days = [
        date(2025, 10, 23),
        date(2026, 9, 23),
        date(2026, 10, 16),
        date(2026, 10, 22),
        date(2026, 10, 23),
    ]
    return session_calendar(
        days,
        time(9, 0),
        time(17, 30),
        date(2025, 10, 1),
        date(2026, 10, 23),
    )


def test_sheet_changes_appear_after_manual_refresh_without_write_access():
    payload_v1 = _sheet_payload(
        [
            [
                "Synthetik Aktie",
                "SYN-A",
                "Aktie",
                10,
                "FRA:SYNA",
                "EUR",
                100,
                1000,
                800,
                25,
                200,
                "Tech",
                100,
            ],
        ]
    )
    payload_v2 = _sheet_payload(
        [
            [
                "Synthetik Aktie",
                "SYN-A",
                "Aktie",
                15,
                "FRA:SYNA",
                "EUR",
                100,
                1500,
                1200,
                25,
                300,
                "Tech",
                75,
            ],
            [
                "Synthetik ETF",
                "SYN-E",
                "ETF",
                5,
                "FRA:SYNE",
                "EUR",
                100,
                500,
                450,
                11,
                50,
                "Welt",
                25,
            ],
        ]
    )
    session = _ReadOnlySheetSession(
        [
            _ReadOnlySheetResponse(200, payload_v1),
            _ReadOnlySheetResponse(200, payload_v2),
        ]
    )
    adapter = SheetsAdapter(
        source=SourceConfig(
            kind=SourceKind.GOOGLE_SHEETS,
            spreadsheet_id="synthetic_sheet_id_123",
            worksheet_range="Bestand!A1:M20",
        ),
        metadata=_sheet_metadata(),
        session=session,
    )
    now = T0
    provider = _FakeMarketProvider()
    cache = QuoteCache(
        provider=provider,
        ttls=CacheTTLs(quotes_seconds=300, history_seconds=3600, holdings_seconds=300),
        clock=lambda: now,
    )
    coordinator = PortfolioRefreshCoordinator(
        adapter.read,
        cache,
        clock=lambda: now,
    )

    bundle_initial = coordinator.load(refresh=False)
    assert bundle_initial.available
    assert bundle_initial.valuation.total_eur == Decimal("1000")
    assert len(bundle_initial.valuation.groups) == 1
    assert len(session.calls) == 1
    assert session.calls[0]["method"] == "GET"

    # Without refresh=True, TTL keeps the first snapshot without another GET.
    now = T0 + timedelta(seconds=30)
    bundle_cached = coordinator.load(refresh=False)
    assert len(session.calls) == 1
    assert bundle_cached.valuation.total_eur == Decimal("1000")

    # Manual refresh=True bypasses TTL, issues read-only GET, and swaps atomically.
    now = T0 + timedelta(minutes=2)
    bundle_refreshed = coordinator.load(refresh=True)
    assert len(session.calls) == 2
    assert all(call["method"] == "GET" for call in session.calls)
    assert not hasattr(session, "post")
    assert not hasattr(session, "put")
    assert bundle_refreshed.available
    assert not bundle_refreshed.using_cached_stand
    assert bundle_refreshed.valuation.total_eur == Decimal("2000")
    assert len(bundle_refreshed.valuation.groups) == 2
    assert bundle_refreshed.snapshot.attempted_at == now


def test_manual_sheet_values_without_timestamp_are_named_sheet_wert_kursstand_unbekannt():
    payload = _sheet_payload(
        [
            [
                "Manuelles Derivat",
                "DER-1",
                "Derivat",
                4,
                "",
                "EUR",
                100,
                400,
                "k. A.",
                "",
                "",
                "Derivate",
                100,
            ],
        ]
    )
    session = _ReadOnlySheetSession([_ReadOnlySheetResponse(200, payload)])
    adapter = SheetsAdapter(
        source=SourceConfig(
            kind=SourceKind.GOOGLE_SHEETS,
            spreadsheet_id="synthetic_sheet_id_123",
            worksheet_range="Bestand!A1:M20",
        ),
        metadata=_sheet_metadata(),
        session=session,
    )
    cache = QuoteCache(provider=_FakeMarketProvider(), clock=lambda: T0)
    coordinator = PortfolioRefreshCoordinator(
        adapter.read, cache, clock=lambda: T0
    )

    bundle = coordinator.load(refresh=False)
    perfs = bundle.performances_for("Kursentwicklung", "Heute")
    reasons = bundle.reasons_for("Kursentwicklung", "Heute")
    rows = build_table_rows(
        bundle.valuation, perfs, period_reasons=reasons
    )
    kpis = build_kpis(bundle.valuation, perfs)
    notes = build_status_notes(
        bundle.valuation, perfs, period_reasons=reasons
    )

    assert len(rows) == 1
    assert "Keine Kurszuordnung" in rows[0].status
    assert reasons == ("unverified_instrument",)
    assert SHEET_UNKNOWN_QUOTE_STATUS in rows[0].status
    assert SHEET_UNKNOWN_QUOTE_STATUS in kpis.total_status_note
    assert any(SHEET_UNKNOWN_QUOTE_STATUS in note for note in notes)


def _course_status_bundle(
    *, mapped, failure_code="provider_failure", sheet_price="100", history_code=None
):
    """Refresh a synthetic identified position without any provider network access."""
    instrument = Instrument(
        InstrumentType.STOCK,
        instrument_id="SYNTHETIC-IDENTITY",
        trading_venue="FRA",
        quote_unit=EUR,
    )
    listing = VerifiedListing(
        instrument=Instrument(
            InstrumentType.STOCK,
            instrument_id=instrument.instrument_id,
            trading_venue="FRA",
            quote_unit=EUR,
            provider_mapping=ProviderMapping("yfinance", "SYNTHETIC.F"),
        ),
        equity_form=EquityForm.SHARE,
    )
    position = Position(
        name="Synthetische Position",
        quantity=Decimal(2),
        instrument=instrument,
        category=None,
        source_row=2,
        imported_at=T0,
        valuation=(
            Valuation(Decimal(sheet_price), ValueStatus.AVAILABLE,
                      Provenance(ValuationSource.SHEET, T0), EUR)
            if sheet_price is not None else None
        ),
    )
    snapshot = PortfolioSnapshot(T0, SnapshotStatus.COMPLETE, (position,))

    class RejectedQuoteProvider(_FakeMarketProvider):
        def latest_quote(self, instrument):
            if history_code is not None:
                return super().latest_quote(instrument)
            self.quote_calls += 1
            return PriceObservation(
                Valuation(
                    None,
                    ValueStatus.MISSING,
                    Provenance(ValuationSource.PROVIDER, T0, provider="yfinance"),
                    warnings=(DataWarning(failure_code, "SYNTHETIC PRIVATE DETAIL"),),
                )
            )

        def history(self, instrument, start, end, *, minimum_points=2):
            return PriceHistory(
                ValueStatus.MISSING,
                Provenance(ValuationSource.PROVIDER, T0, provider="yfinance"),
                warnings=(DataWarning(history_code, "SYNTHETIC PRIVATE HISTORY"),),
            )

    provider = RejectedQuoteProvider()
    coordinator = PortfolioRefreshCoordinator(
        lambda _: snapshot,
        QuoteCache(provider, clock=lambda: T0),
        listings=(listing,) if mapped else (),
        # Missing calendar must not mask mapping/rejected-quote reasons.
        calendars={"FRA": _sample_calendar()} if history_code is not None else {},
        clock=lambda: T0,
    )
    return coordinator.load(), provider


@pytest.mark.parametrize("sheet_price", ["100", None])
def test_missing_listing_is_named_even_with_identity_and_without_a_sheet_value(sheet_price):
    bundle, provider = _course_status_bundle(mapped=False, sheet_price=sheet_price)
    assert bundle.available
    assert provider.quote_calls == 0
    for period in ("Heute", "Woche", "Monat", "Jahr"):
        assert bundle.reasons_for("Kursentwicklung", period) == ("unverified_instrument",)
        rows = build_table_rows(
            bundle.valuation, bundle.performances_for("Kursentwicklung", period),
            period_reasons=bundle.reasons_for("Kursentwicklung", period),
        )
        assert "Keine Kurszuordnung" in rows[0].status
        assert "Kursabruf fehlgeschlagen" not in rows[0].status
        assert "Keine Kurszuordnung" in " ".join(build_position_details(rows)[0])
        assert "Keine Kurszuordnung" in " ".join(build_status_notes(
            bundle.valuation, bundle.performances_for("Kursentwicklung", period),
            period_reasons=bundle.reasons_for("Kursentwicklung", period),
        ))
    assert "Keine Kurszuordnung" in build_table_rows(
        bundle.valuation, (Performance(None),), is_personal_view=True
    )[0].status


@pytest.mark.parametrize("sheet_price", ["100", None])
@pytest.mark.parametrize(("code", "reason"), [
    ("instrument_metadata_mismatch", "Instrument oder Handelsplatz weicht ab"),
    ("provider_failure", "Kursanbieter nicht erreichbar"),
    ("unknown_or_conflicting_unit", "Währung oder Kurseinheit unklar"),
    ("provider_backoff", "Abruf vorübergehend pausiert"),
    ("SYNTHETIC-UNKNOWN-CODE", "Kurs nicht verfügbar"),
])
def test_failed_listing_retains_codes_and_shows_only_safe_german_causes(code, reason, sheet_price):
    bundle, provider = _course_status_bundle(
        mapped=True, failure_code=code, sheet_price=sheet_price
    )
    assert bundle.available
    assert provider.quote_calls == 1
    assert code in {w.code for w in bundle.snapshot.positions[0].valuation.warnings}
    for period in ("Heute", "Woche", "Monat", "Jahr"):
        assert bundle.reasons_for("Kursentwicklung", period) == ("quote_unavailable",)
    perfs = bundle.performances_for("Kursentwicklung", "Heute")
    reasons = bundle.reasons_for("Kursentwicklung", "Heute")
    rows = build_table_rows(bundle.valuation, perfs, period_reasons=reasons)
    expected = f"Kursabruf fehlgeschlagen ({reason})"
    details = build_position_details(rows)
    notes = build_status_notes(bundle.valuation, perfs, period_reasons=reasons)
    for text in (rows[0].status, " ".join(details[0]), " ".join(notes)):
        assert expected in text
        assert "Keine Kurszuordnung" not in text
        assert code not in text
        assert "SYNTHETIC PRIVATE DETAIL" not in text
        assert "Keine Kurshistorie bei Sheet-Bewertung" not in text
    assert expected in build_table_rows(
        bundle.valuation, perfs, is_personal_view=True
    )[0].status


@pytest.mark.parametrize(("code", "reason"), [
    ("invalid_price", "Kurs ungültig"),
    ("provider_failure", "Kursanbieter nicht erreichbar"),
    ("SYNTHETIC-UNKNOWN-HISTORY", "Keine geprüften Tageskurse"),
])
def test_rejected_history_retains_safe_cause_in_all_status_surfaces(code, reason):
    bundle, provider = _course_status_bundle(mapped=True, history_code=code)
    assert bundle.available
    assert provider.quote_calls == 1
    assert bundle.snapshot.positions[0].valuation.provenance.source is ValuationSource.PROVIDER
    for period in ("Heute", "Woche", "Monat", "Jahr"):
        perfs = bundle.performances_for("Kursentwicklung", period)
        reasons = bundle.reasons_for("Kursentwicklung", period)
        assert reasons == ("history_unavailable",)
        assert perfs[0].percent is None
        assert code in {warning.code for warning in perfs[0].warnings}
        rows = build_table_rows(bundle.valuation, perfs, period_reasons=reasons)
        details = build_position_details(rows)
        notes = build_status_notes(bundle.valuation, perfs, period_reasons=reasons)
        for text in (rows[0].status, " ".join(details[0]), " ".join(notes)):
            assert f"Kurshistorie nicht verfügbar ({reason})" in text
            assert "Kursabruf fehlgeschlagen" not in text
            assert code not in text
            assert "SYNTHETIC PRIVATE HISTORY" not in text


def test_refresh_failure_retains_previous_atomic_bundle_with_age_or_shows_unavailable():
    payload_ok = _sheet_payload(
        [
            [
                "Synthetik Aktie",
                "SYN-A",
                "Aktie",
                10,
                "FRA:SYNA",
                "EUR",
                100,
                1000,
                800,
                25,
                200,
                "Tech",
                100,
            ],
        ]
    )
    session_fail_first = _ReadOnlySheetSession(
        [_ReadOnlySheetResponse(503, {})]
    )
    adapter_fail_first = SheetsAdapter(
        source=SourceConfig(
            kind=SourceKind.GOOGLE_SHEETS,
            spreadsheet_id="synthetic_sheet_id_123",
            worksheet_range="Bestand!A1:M20",
        ),
        metadata=_sheet_metadata(),
        session=session_fail_first,
    )
    now = T0
    cache_fail_first = QuoteCache(
        provider=_FakeMarketProvider(), clock=lambda: now
    )
    coord_fail_first = PortfolioRefreshCoordinator(
        adapter_fail_first.read, cache_fail_first, clock=lambda: now
    )

    unavailable_bundle = coord_fail_first.load(refresh=False)
    assert not unavailable_bundle.available
    assert DATA_UNAVAILABLE_TEXT in (unavailable_bundle.status_message or "")

    # Now test: initial success at T0, followed by failure at T0 + 15 minutes.
    session_then_fail = _ReadOnlySheetSession(
        [
            _ReadOnlySheetResponse(200, payload_ok),
            _ReadOnlySheetResponse(503, {}),
        ]
    )
    adapter_then_fail = SheetsAdapter(
        source=SourceConfig(
            kind=SourceKind.GOOGLE_SHEETS,
            spreadsheet_id="synthetic_sheet_id_123",
            worksheet_range="Bestand!A1:M20",
        ),
        metadata=_sheet_metadata(),
        session=session_then_fail,
    )
    cache_then_fail = QuoteCache(
        provider=_FakeMarketProvider(), clock=lambda: now
    )
    coord_then_fail = PortfolioRefreshCoordinator(
        adapter_then_fail.read, cache_then_fail, clock=lambda: now
    )

    first_bundle = coord_then_fail.load(refresh=False)
    assert first_bundle.available
    assert not first_bundle.using_cached_stand
    assert first_bundle.valuation.total_eur == Decimal("1000")

    now = T0 + timedelta(minutes=15)
    retained_bundle = coord_then_fail.load(refresh=True)
    assert retained_bundle.available
    assert retained_bundle.using_cached_stand
    assert retained_bundle.snapshot == first_bundle.snapshot
    assert retained_bundle.valuation == first_bundle.valuation
    assert retained_bundle.snapshot.attempted_at == T0
    assert "vor 15 Min." in retained_bundle.age_label(now)
    assert "vor 15 Min." in (retained_bundle.status_message or "")


def test_partial_coverage_and_status_reasons_for_history_identifier_and_cost():
    mapped_inst = Instrument(
        InstrumentType.STOCK,
        instrument_id="SYN-STOCK-1",
        display_ticker="SYN1",
        trading_venue="XETR",
        quote_unit=EUR,
        provider_mapping= VerifiedListing(
            instrument=Instrument(
                InstrumentType.STOCK,
                instrument_id="SYN-STOCK-1",
                display_ticker="SYN1",
                trading_venue="XETR",
                quote_unit=EUR,
            ),
            equity_form=EquityForm.SHARE,
        ).instrument.provider_mapping,
    )
    _ = mapped_inst
    p_stale_hist = Position(
        name="Alt-Kurs Aktie",
        quantity=Decimal("5"),
        instrument=Instrument(InstrumentType.STOCK, display_ticker="ALT"),
        category="Tech",
        source_row=1,
        imported_at=T0,
        cost_basis=CostBasis(Decimal("400"), CostBasisStatus.UNVERIFIED),
        valuation=Valuation(
            Decimal("100"),
            ValueStatus.AVAILABLE,
            Provenance(ValuationSource.SHEET, T0),
            EUR,
        ),
    )
    p_missing_price = Position(
        name="Unbewertete Position",
        quantity=Decimal("2"),
        instrument=Instrument(InstrumentType.ETF, display_ticker="UNV"),
        category="Welt",
        source_row=2,
        imported_at=T0,
        valuation=None,
    )
    snapshot = PortfolioSnapshot(
        T0, SnapshotStatus.COMPLETE, (p_stale_hist, p_missing_price)
    )
    valuation = value_portfolio(snapshot, ())
    perfs = (Performance(None), Performance(None))
    reasons = ("stale_quote", "unavailable_value")

    kpis = build_kpis(valuation, perfs)
    assert kpis.is_partial_total
    assert "Teilabdeckung (1 von 2 Positionen)" in kpis.total_status_note
    assert SHEET_UNKNOWN_QUOTE_STATUS in kpis.total_status_note

    course_rows = build_table_rows(
        valuation, perfs, period_reasons=reasons
    )
    assert "Kurs/Historie veraltet" in course_rows[0].status
    assert course_rows[1].status == "Kurs fehlt"

    personal_rows = build_table_rows(
        valuation, perfs, is_personal_view=True
    )
    assert "Einstand ungeprüft (Teilverkauf)" in personal_rows[0].status


def test_ui_renders_unavailable_error_when_failed_snapshot_has_no_prior_state():
    def app_script():
        from datetime import UTC, datetime

        from portfolio_dashboard.models import (
            DataWarning,
            PortfolioSnapshot,
            SnapshotStatus,
        )
        from portfolio_dashboard.treemap import Performance
        from portfolio_dashboard.ui import render_dashboard
        from portfolio_dashboard.valuations import value_portfolio

        failed = PortfolioSnapshot(
            datetime(2026, 10, 23, 12, 0, tzinfo=UTC),
            SnapshotStatus.FAILED,
            warnings=(DataWarning("sheet_unavailable", "Service unavailable"),),
        )
        val = value_portfolio(failed, ())
        render_dashboard(failed, None, val, lambda v, p: (Performance(None),))

    app = AppTest.from_function(app_script).run()
    assert not app.exception
    assert len(app.error) == 1
    assert DATA_UNAVAILABLE_TEXT in app.error[0].value
    assert len(app.dataframe) == 0


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (12, "vor 12 Sek."),
        (180, "vor 3 Min."),
        (3600, "vor 1 Std."),
        (5400, "vor 1 Std. 30 Min."),
        (172800, "vor 2 Tag(en)"),
    ],
)
def test_format_snapshot_age_in_german(seconds, expected):
    assert format_snapshot_age(T0, T0 + timedelta(seconds=seconds)) == expected


def test_refresh_button_in_demo_dashboard_runs_cleanly_offline():
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(str(app_path)).run()
    assert not app.exception
    app.button(key=REFRESH_BUTTON_KEY).click().run()
    assert not app.exception
    assert any(
        "Synthetischer Demo-Stand geprüft" in c.value for c in app.caption
    )
    _ = render_dashboard
    _ = _sample_calendar
