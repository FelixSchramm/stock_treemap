"""End-to-end domain regression tests across import, mapping, cache, valuation, returns, periods and treemap.

All fixtures are synthetic, deterministic and time-injected; no network or provider calls occur.
"""

import subprocess
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from portfolio_dashboard.config import CacheTTLs
from portfolio_dashboard.instruments import (
    EquityForm,
    VerifiedListing,
    lookup_provider_mapping,
    resolve_listing,
)
from portfolio_dashboard.market_data import (
    AdjustmentBasis,
    FxObservation,
    HistoryPoint,
    PriceHistory,
    PriceObservation,
    VerifiedFxPair,
)
from portfolio_dashboard.models import (
    DataWarning,
    Instrument,
    InstrumentType,
    PortfolioSnapshot,
    Position,
    PositionActivity,
    Provenance,
    ProviderMapping,
    QuoteUnit,
    SnapshotStatus,
    Valuation,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.price_periods import (
    PricePeriod,
    price_period_returns,
    session_calendar,
)
from portfolio_dashboard.quote_cache import (
    QuoteCache,
    choose_valuation,
    supports_period_return,
)
from portfolio_dashboard.returns import PersonalReturnStatus, portfolio_returns
from portfolio_dashboard.sheet_parser import (
    SHEET_HEADERS,
    NumberLocale,
    SheetMetadata,
    parse_sheet_rows,
)
from portfolio_dashboard.treemap import (
    MISSING_COLOR,
    Performance,
    build_treemap,
    performance_from_period,
    personal_performances,
)
from portfolio_dashboard.valuations import EurValuationStatus, FxRate, value_portfolio

IMPORTED_AT = datetime(2026, 10, 23, 14, 30, tzinfo=UTC)
EVAL_NOW = datetime(2026, 10, 23, 18, 0, tzinfo=UTC)
EUR_UNIT = QuoteUnit("EUR", Decimal("1"))
USD_UNIT = QuoteUnit("USD", Decimal("1"))
GBX_UNIT = QuoteUnit("GBP", Decimal("0.01"))
GBP_UNIT = QuoteUnit("GBP", Decimal("1"))
CHF_UNIT = QuoteUnit("CHF", Decimal("1"))
BASIS = AdjustmentBasis.YAHOO_SPLIT_ADJUSTED_PRICE

USD_EUR_PAIR = VerifiedFxPair(
    USD_UNIT,
    EUR_UNIT,
    ProviderMapping("synthetic", "USDEUR=X"),
    "SYNTH-FX",
)
GBP_EUR_PAIR = VerifiedFxPair(
    GBP_UNIT,
    EUR_UNIT,
    ProviderMapping("synthetic", "GBPEUR=X"),
    "SYNTH-FX",
)


def _listing_registry() -> dict[str, VerifiedListing]:
    """Return synthetic verified listings covering EUR, USD, GBp, multi-venue, derivatives and CHF."""
    return {
        "eur_alpha": VerifiedListing(
            Instrument(
                InstrumentType.STOCK,
                instrument_id="synth-eur-alpha",
                display_ticker="ALFA",
                source_symbol="FRA:ALFA",
                trading_venue="XETRA",
                quote_unit=EUR_UNIT,
                provider_mapping=ProviderMapping("synthetic", "ALFA.DE"),
            ),
            equity_form=EquityForm.SHARE,
            isin="DE0000000001",
        ),
        "usd_beta": VerifiedListing(
            Instrument(
                InstrumentType.STOCK,
                instrument_id="synth-usd-beta",
                display_ticker="BETA",
                source_symbol="NASDAQ:BETA",
                trading_venue="NMS",
                quote_unit=USD_UNIT,
                provider_mapping=ProviderMapping("synthetic", "BETA"),
            ),
            equity_form=EquityForm.SHARE,
            isin="US0000000002",
        ),
        "gbx_etf": VerifiedListing(
            Instrument(
                InstrumentType.ETF,
                instrument_id="synth-gbx-etf",
                display_ticker="UKFT",
                source_symbol="LON:UKFT",
                trading_venue="LSE",
                quote_unit=GBX_UNIT,
                provider_mapping=ProviderMapping("synthetic", "UKFT.L"),
            ),
            isin="IE0000000003",
            share_class="Synthetisch Thesaurierend",
        ),
        "multi_xetra": VerifiedListing(
            Instrument(
                InstrumentType.STOCK,
                instrument_id="synth-multi-hold",
                display_ticker="MULT",
                source_symbol="FRA:MULT",
                trading_venue="XETRA",
                quote_unit=EUR_UNIT,
                provider_mapping=ProviderMapping("synthetic", "MULT.DE"),
            ),
            equity_form=EquityForm.SHARE,
            isin="DE0000000004",
        ),
        "multi_tradegate": VerifiedListing(
            Instrument(
                InstrumentType.STOCK,
                instrument_id="synth-multi-hold",
                display_ticker="MULT",
                source_symbol="TG:MULT",
                trading_venue="TRADEGATE",
                quote_unit=EUR_UNIT,
                provider_mapping=ProviderMapping("synthetic", "MULT.TG"),
            ),
            equity_form=EquityForm.SHARE,
            isin="DE0000000004",
        ),
        "partial_cost_a": VerifiedListing(
            Instrument(
                InstrumentType.STOCK,
                instrument_id="synth-partial-cost",
                display_ticker="PART",
                source_symbol="FRA:PART",
                trading_venue="XETRA",
                quote_unit=EUR_UNIT,
                provider_mapping=ProviderMapping("synthetic", "PART.DE"),
            ),
            equity_form=EquityForm.SHARE,
            isin="DE0000000005",
        ),
        "deriv_sheet": VerifiedListing(
            Instrument(
                InstrumentType.DERIVATIVE,
                instrument_id="synth-deriv-sheet",
                display_ticker="ALFA",  # Shares display ticker with underlying stock.
                source_symbol="FRA:ALFA",
                trading_venue="EUWAX",
            ),
            product_id="SYNTH-CERT-001",
        ),
        "missing_price": VerifiedListing(
            Instrument(
                InstrumentType.STOCK,
                instrument_id="synth-missing-price",
                display_ticker="MISS",
                source_symbol="FRA:MISS",
                trading_venue="XETRA",
                quote_unit=EUR_UNIT,
                provider_mapping=ProviderMapping("synthetic", "MISS.DE"),
            ),
            equity_form=EquityForm.SHARE,
            isin="DE0000000006",
        ),
        "chf_no_fx": VerifiedListing(
            Instrument(
                InstrumentType.ETF,
                instrument_id="synth-chf-etf",
                display_ticker="SWIS",
                source_symbol="SWX:SWIS",
                trading_venue="EBS",
                quote_unit=CHF_UNIT,
                provider_mapping=ProviderMapping("synthetic", "SWIS.SW"),
            ),
            isin="CH0000000007",
            share_class="Synthetisch CHF",
        ),
        "zero_price": VerifiedListing(
            Instrument(
                InstrumentType.DERIVATIVE,
                instrument_id="synth-zero-warrant",
                display_ticker="ZERO",
                source_symbol="EUWAX:ZERO",
                trading_venue="EUWAX",
                quote_unit=EUR_UNIT,
                provider_mapping=ProviderMapping("synthetic", "ZERO.EU"),
            ),
            product_id="SYNTH-WARRANT-000",
        ),
    }


def _sheet_row(
    position: str,
    ticker: str,
    kind: str,
    quantity: str,
    symbol: str,
    currency: str,
    sheet_price_eur: str | None,
    cost_eur: str | None,
    category: str = "Synthetisch",
    current_eur: str = "999.999,00",
    gv_pct: str = "99,0",
    gv_eur: str = "999,00",
    weight_pct: str = "50,0",
) -> list[str | None]:
    """Build one A-M sheet row in German locale with deliberately distinct reference cells."""
    cells = {
        "Position": position,
        "Ticker": ticker,
        "Typ": kind,
        "Anzahl": quantity,
        "Symbol (Google Finance)": symbol,
        "Währung": currency,
        "Kurs (EUR)": sheet_price_eur,
        "Aktueller Wert (EUR)": current_eur,
        "Investiert (EUR)": cost_eur,
        "G/V (%)": gv_pct,
        "G/V (EUR)": gv_eur,
        "Kategorie": category,
        "Gewicht (%)": weight_pct,
    }
    return [cells[header] for header in SHEET_HEADERS]


def _synthetic_sheet_rows() -> list[list[str | None]]:
    """Return the documented 15-row regression table (rows 2..16 after header row 1)."""
    return [
        # Row 2: empty row -> excluded ("empty")
        [""] * 13,
        # Row 3: summary row -> excluded ("summary")
        ["Summe Aktien", "", "", "", "", "", "", "450,00", "400,00", "12,5", "50,00", "", "100"],
        # Row 4: zero holding -> excluded ("zero_quantity")
        _sheet_row("Verkaufte Altposition", "SOLD", "Aktie", "0", "FRA:SOLD", "EUR", "15,00", "150,00"),
        # Row 5: EUR stock Alpha (10 * 12 EUR = 120 EUR, cost 100 EUR -> +20 %)
        _sheet_row("EUR-Aktie Alpha", "ALFA", "Aktie", "10", "FRA:ALFA", "EUR", "11,50", "100,00"),
        # Row 6: USD stock Beta (8.25 * 50 USD * 0.80 = 330 EUR, cost 300 EUR -> +10 %)
        _sheet_row("USD-Aktie Beta", "BETA", "Aktie", "8,25", "NASDAQ:BETA", "USD", "39,00", "300,00"),
        # Row 7: UK ETF in GBp (50 * 250 GBp * 0.01 * 1.20 = 150 EUR, cost 120 EUR -> +25 %)
        _sheet_row("Britischer ETF", "UKFT", "ETF", "50", "LON:UKFT", "GBP", "2,90", "120,00"),
        # Row 8: Partial position 1 of Multi-Hold on XETRA (4 * 25 EUR = 100 EUR, cost 80 EUR -> +25 %)
        _sheet_row("Mehrfach-Aktie", "MULT", "Aktie", "4", "FRA:MULT", "EUR", "24,00", "80,00"),
        # Row 9: Partial position 2 of Multi-Hold on Tradegate (6 * 25 EUR = 150 EUR, cost 120 EUR -> +25 %)
        _sheet_row("Mehrfach-Aktie", "MULT", "Aktie", "6", "TG:MULT", "EUR", "24,00", "120,00"),
        # Row 10: Partial position A of Partial-Cost (2 * 40 EUR = 80 EUR, cost 50 EUR -> +60 %)
        _sheet_row("Teilverkauf-Aktie", "PART", "Aktie", "2", "FRA:PART", "EUR", "38,00", "50,00"),
        # Row 11: Partial position B of Partial-Cost (3 * 40 EUR = 120 EUR, missing cost -> group n. v.)
        _sheet_row("Teilverkauf-Aktie", "PART", "Aktie", "3", "FRA:PART", "EUR", "38,00", "k. A."),
        # Row 12: Derivative with Sheet EUR fallback (5 * 9 EUR = 45 EUR, no provider coverage, missing cost)
        _sheet_row("Zertifikat auf Alpha", "ALFA", "Derivat", "5", "FRA:ALFA", "EUR", "9,00 €", None),
        # Row 13: Stock without provider or sheet price (quantity 7, cost 70 EUR -> excluded from totals)
        _sheet_row("Datenluecke-Aktie", "MISS", "Aktie", "7", "FRA:MISS", "EUR", "k. A.", "70,00"),
        # Row 14: CHF ETF with provider price 100 CHF but no CHF->EUR FX rate and no sheet fallback
        _sheet_row("Schweizer ETF", "SWIS", "ETF", "2", "SWX:SWIS", "CHF", None, "150,00"),
        # Row 15: Expired warrant with evidenced 0 EUR provider price (10 * 0 = 0 EUR, cost 40 EUR -> -100 %)
        _sheet_row("Optionsschein Nullwert", "ZERO", "Derivat", "10", "EUWAX:ZERO", "EUR", "0,50", "40,00"),
        # Row 16: Negative booking -> kept as NEGATIVE, excluded from active valuation and cost basis
        _sheet_row("Storno-Buchung", "NEGA", "Aktie", "-2", "FRA:NEGA", "EUR", "10,00", "20,00", current_eur="-20,00"),
    ]


class SyntheticClock:
    """Deterministic manual clock for cache and provider tests."""

    def __init__(self, initial: datetime) -> None:
        self.now = initial

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class RegressionMarketDataProvider:
    """Deterministic offline MarketDataProvider for end-to-end regression scenarios."""

    def __init__(
        self,
        clock: SyntheticClock,
        registry: dict[str, VerifiedListing],
    ) -> None:
        self.clock = clock
        self.registry = registry
        self.fail_symbols: set[str] = set()
        self.quote_prices: dict[str, tuple[Decimal, QuoteUnit, datetime]] = {
            "ALFA.DE": (
                Decimal("12.00"),
                EUR_UNIT,
                datetime(2026, 10, 23, 15, 30, tzinfo=UTC),
            ),
            "BETA": (
                Decimal("50.00"),
                USD_UNIT,
                datetime(2026, 10, 23, 17, 55, tzinfo=UTC),
            ),
            "UKFT.L": (
                Decimal("250"),
                GBX_UNIT,
                datetime(2026, 10, 23, 14, 0, tzinfo=UTC),
            ),
            "MULT.DE": (
                Decimal("25.00"),
                EUR_UNIT,
                datetime(2026, 10, 23, 15, 30, tzinfo=UTC),
            ),
            "MULT.TG": (
                Decimal("25.00"),
                EUR_UNIT,
                datetime(2026, 10, 23, 15, 25, tzinfo=UTC),
            ),
            "PART.DE": (
                Decimal("40.00"),
                EUR_UNIT,
                datetime(2026, 10, 23, 15, 30, tzinfo=UTC),
            ),
            "SWIS.SW": (
                Decimal("100.00"),
                CHF_UNIT,
                datetime(2026, 10, 23, 15, 30, tzinfo=UTC),
            ),
            "ZERO.EU": (
                Decimal("0.00"),
                EUR_UNIT,
                datetime(2026, 10, 23, 15, 30, tzinfo=UTC),
            ),
        }

    def latest_quote(self, instrument: Instrument) -> PriceObservation:
        mapping = instrument.provider_mapping
        retrieved = self.clock()
        if mapping is None:
            return PriceObservation(
                Valuation(
                    None,
                    ValueStatus.MISSING,
                    Provenance(ValuationSource.PROVIDER, retrieved, provider="synthetic"),
                    warnings=(DataWarning("no_provider_coverage", "Kein Provider-Mapping"),),
                )
            )
        symbol = mapping.symbol
        if symbol in self.fail_symbols or symbol not in self.quote_prices:
            return PriceObservation(
                Valuation(
                    None,
                    ValueStatus.MISSING,
                    Provenance(ValuationSource.PROVIDER, retrieved, provider="synthetic"),
                    warnings=(DataWarning("provider_failure", "Synthetischer Ausfall"),),
                )
            )
        price, unit, observed = self.quote_prices[symbol]
        return PriceObservation(
            Valuation(
                price,
                ValueStatus.AVAILABLE,
                Provenance(ValuationSource.PROVIDER, retrieved, observed, "synthetic"),
                unit,
            ),
            BASIS,
        )

    def fx(self, pair: VerifiedFxPair) -> FxObservation:
        retrieved = self.clock()
        rates = {
            "USDEUR=X": Decimal("0.80"),
            "GBPEUR=X": Decimal("1.20"),
        }
        symbol = pair.mapping.symbol
        if symbol in self.fail_symbols or symbol not in rates:
            return FxObservation(
                pair,
                None,
                ValueStatus.MISSING,
                Provenance(ValuationSource.PROVIDER, retrieved, provider="synthetic"),
                (DataWarning("provider_failure", "Synthetischer FX-Ausfall"),),
            )
        observed = datetime(2026, 10, 23, 17, 50, tzinfo=UTC)
        return FxObservation(
            pair,
            rates[symbol],
            ValueStatus.AVAILABLE,
            Provenance(ValuationSource.PROVIDER, retrieved, observed, "synthetic"),
        )

    def history(
        self, instrument: Instrument, start: date, end: date, *, minimum_points: int = 2
    ) -> PriceHistory:
        raise NotImplementedError


def _assign_verified_listings(
    snapshot: PortfolioSnapshot,
    row_to_listing: dict[int, VerifiedListing],
) -> PortfolioSnapshot:
    """Attach verified instrument identities to the corresponding source rows."""
    updated: list[Position] = []
    for pos in snapshot.positions:
        listing = row_to_listing.get(pos.source_row)
        if listing is None:
            updated.append(pos)
            continue
        updated.append(replace(pos, instrument=listing.instrument))
    return replace(snapshot, positions=tuple(updated))


class TestIntegratedDomainPipeline:
    """End-to-end verification of import, mapping, cache, valuation, returns, periods and treemap."""

    def _run_pipeline(self, *, verify_cost_basis: bool = True):
        registry_map = _listing_registry()
        listings = tuple(registry_map.values())
        metadata = SheetMetadata(
            NumberLocale.GERMAN,
            cost_basis_verified_remaining=verify_cost_basis,
        )
        parsed = parse_sheet_rows(
            SHEET_HEADERS,
            _synthetic_sheet_rows(),
            imported_at=IMPORTED_AT,
            metadata=metadata,
        )
        row_to_listing = {
            5: registry_map["eur_alpha"],
            6: registry_map["usd_beta"],
            7: registry_map["gbx_etf"],
            8: registry_map["multi_xetra"],
            9: registry_map["multi_tradegate"],
            10: registry_map["partial_cost_a"],
            11: registry_map["partial_cost_a"],
            12: registry_map["deriv_sheet"],
            13: registry_map["missing_price"],
            14: registry_map["chf_no_fx"],
            15: registry_map["zero_price"],
            # Row 16 (negative) stays unmapped to verify unmapped negative handling.
        }
        mapped_snapshot = _assign_verified_listings(parsed.snapshot, row_to_listing)

        # Warm the cache for UKFT.L at t0, then simulate a provider failure for UKFT.L
        # after TTL expiry so UKFT.L exercises the CACHED_PROVIDER path while MISS.DE
        # fails without prior success and ALFA derivative falls back to its EUR Sheet price.
        clock = SyntheticClock(EVAL_NOW - timedelta(seconds=120))
        provider = RegressionMarketDataProvider(clock, registry_map)
        cache = QuoteCache(
            provider,
            ttls=CacheTTLs(quotes_seconds=60, holdings_seconds=300, history_seconds=21600),
            clock=clock,
            provider_failure_threshold=5,
        )
        cache.quote(registry_map["gbx_etf"].instrument)
        clock.advance(120)  # Now at EVAL_NOW; UKFT.L TTL expired.
        provider.fail_symbols.add("UKFT.L")

        valued_positions: list[Position] = []
        for pos in mapped_snapshot.positions:
            if pos.activity is not PositionActivity.ACTIVE:
                valued_positions.append(pos)
                continue
            listing = resolve_listing(pos.instrument, listings)
            if listing is not None and lookup_provider_mapping(pos.instrument, listings) is not None:
                market_val = cache.quote(listing.instrument).valuation
            else:
                market_val = Valuation(
                    None,
                    ValueStatus.MISSING,
                    Provenance(ValuationSource.PROVIDER, clock(), provider="synthetic"),
                    warnings=(DataWarning("no_provider_coverage", "Kein Provider-Mapping"),),
                )
            chosen = choose_valuation(market_val, pos.valuation)
            valued_positions.append(replace(pos, valuation=chosen))

        usd_obs = cache.fx(USD_EUR_PAIR)
        gbp_obs = cache.fx(GBP_EUR_PAIR)
        fx_rates = {
            "USD": FxRate("USD", "EUR", usd_obs.rate, usd_obs.provenance, usd_obs.warnings),
            "GBP": FxRate("GBP", "EUR", gbp_obs.rate, gbp_obs.provenance, gbp_obs.warnings),
        }

        final_snapshot = replace(mapped_snapshot, positions=tuple(valued_positions))
        valuation = value_portfolio(final_snapshot, listings, fx_rates)
        returns = portfolio_returns(valuation)
        return parsed, valuation, returns, registry_map

    def test_import_exclusions_and_snapshot_boundaries(self) -> None:
        parsed, valuation, _, _ = self._run_pipeline()

        assert parsed.snapshot.status is SnapshotStatus.COMPLETE
        assert [(e.source_row, e.reason) for e in parsed.excluded_rows] == [
            (2, "empty"),
            (3, "summary"),
            (4, "zero_quantity"),
        ]
        assert len(parsed.snapshot.positions) == 12
        assert len(parsed.active_positions) == 11
        assert len(parsed.negative_positions) == 1
        assert parsed.negative_positions[0].source_row == 16
        assert parsed.negative_positions[0].quantity == Decimal("-2")

        # Sheet reference cells (999.999,00 EUR / 99 %) never enter domain valuations.
        assert all(
            ref.current_value_eur == Decimal("999999.00")
            for ref in parsed.references[:-1]
        )
        assert valuation.total_eur == Decimal("1095.00")

    def test_mixed_currencies_minor_units_cached_quotes_and_sheet_fallbacks(self) -> None:
        _, valuation, _, _ = self._run_pipeline()
        by_row = {pv.position.source_row: pv for pv in valuation.positions}

        # Row 5: EUR provider quote (10 * 12 = 120 EUR)
        assert by_row[5].amount_eur == Decimal("120.00")
        assert by_row[5].status is EurValuationStatus.AVAILABLE
        assert by_row[5].fx_rate is None
        assert by_row[5].position.valuation.provenance.source is ValuationSource.PROVIDER

        # Row 6: USD provider quote with directed USD->EUR FX (8.25 * 50 * 0.80 = 330 EUR)
        assert by_row[6].amount_eur == Decimal("330.0000")
        assert by_row[6].fx_rate.base_currency == "USD"
        assert by_row[6].fx_rate.quote_currency == "EUR"

        # Row 7: GBp cached provider quote (50 * 250 * 0.01 * 1.20 = 150 EUR)
        assert by_row[7].amount_eur == Decimal("150.000")
        assert by_row[7].position.valuation.provenance.source is ValuationSource.CACHED_PROVIDER
        assert "cached_provider_value" in {w.code for w in by_row[7].warnings}
        assert supports_period_return(by_row[7].position.valuation) is True

        # Row 12: Derivative without provider mapping uses EUR Sheet fallback (5 * 9 = 45 EUR)
        # Despite sharing display_ticker "ALFA" and source_symbol "FRA:ALFA" with Row 5,
        # it never steals Alpha's 12 EUR stock quote or merges into Alpha's group.
        assert by_row[12].amount_eur == Decimal("45.00")
        assert by_row[12].position.valuation.provenance.source is ValuationSource.SHEET
        assert {
            "price_source_fallback",
            "sheet_value_quote_time_unknown",
            "no_provider_coverage",
        } <= {w.code for w in by_row[12].warnings}
        assert supports_period_return(by_row[12].position.valuation) is False

        # Row 13: Missing both provider and sheet price -> MISSING_PRICE, amount_eur is None
        assert by_row[13].amount_eur is None
        assert by_row[13].status is EurValuationStatus.MISSING_PRICE
        assert by_row[13].weight_fraction is None

        # Row 14: CHF quote available, but CHF->EUR FX missing -> MISSING_FX, amount_eur is None
        assert by_row[14].amount_eur is None
        assert by_row[14].status is EurValuationStatus.MISSING_FX
        assert by_row[14].weight_fraction is None

        # Row 15: Evidenced zero provider price remains AVAILABLE with 0 EUR and 0 weight
        assert by_row[15].amount_eur == Decimal("0.00")
        assert by_row[15].status is EurValuationStatus.AVAILABLE
        assert by_row[15].weight_fraction == Decimal("0")

        # Row 16: Negative quantity is INACTIVE and excluded from active count and totals
        assert by_row[16].amount_eur is None
        assert by_row[16].status is EurValuationStatus.INACTIVE
        assert by_row[16].weight_fraction is None

    def test_partial_positions_personal_returns_and_coverage_invariants(self) -> None:
        _, valuation, returns, _ = self._run_pipeline(verify_cost_basis=True)
        ret_by_row = {pr.valuation.position.source_row: pr for pr in returns.positions}

        # Valuation coverage: 9 of 11 active positions valued -> PARTIAL status
        assert valuation.active_count == 11
        assert valuation.valued_count == 9
        assert valuation.coverage_fraction == Decimal(9) / Decimal(11)
        assert valuation.status is SnapshotStatus.PARTIAL

        # Personal return coverage: 7 of 11 active positions eligible
        # Row 11 (missing cost), Row 12 (missing cost), Row 13 (missing price), Row 14 (missing FX)
        # are excluded from both numerator and denominator of the portfolio return.
        assert returns.total.active_count == 11
        assert returns.total.eligible_count == 7
        assert returns.total.coverage_fraction == Decimal(7) / Decimal(11)
        assert returns.status is SnapshotStatus.PARTIAL

        # Missing values or costs never produce 0 % return.
        for row_num, expected_status in (
            (11, PersonalReturnStatus.MISSING_COST),
            (12, PersonalReturnStatus.MISSING_COST),
            (13, PersonalReturnStatus.UNAVAILABLE_VALUE),
            (14, PersonalReturnStatus.UNAVAILABLE_VALUE),
            (16, PersonalReturnStatus.INACTIVE),
        ):
            assert ret_by_row[row_num].status is expected_status
            assert ret_by_row[row_num].return_percent is None
            assert ret_by_row[row_num].gain_eur is None

        # Evidenced zero price (Row 15) produces -100 %, never None or 0 %.
        assert ret_by_row[15].status is PersonalReturnStatus.AVAILABLE
        assert ret_by_row[15].gain_eur == Decimal("-40.00")
        assert ret_by_row[15].return_percent == Decimal("-100")

        # Exact eligible subtotals (Rows 5, 6, 7, 8, 9, 10, 15):
        # Market value: 120 + 330 + 150 + 100 + 150 + 80 + 0 = 930 EUR
        # Cost basis:   100 + 300 + 120 +  80 + 120 + 50 + 40 = 810 EUR
        # Gain:         +120 EUR -> (120 / 810) * 100 = 400 / 27 %
        assert returns.total.eligible_market_value_eur == Decimal("930.0000")
        assert returns.total.eligible_cost_basis_eur == Decimal("810.00")
        assert returns.total.gain_eur == Decimal("120.0000")
        assert returns.total.return_percent == (Decimal("120") / Decimal("810")) * Decimal("100")

        # Group-level aggregation for multi-row instruments:
        # Group 3 (Rows 8 + 9, SYNTH-MULTI): both rows have verified cost -> +25 %
        multi_group = returns.groups[3]
        assert [m.valuation.position.source_row for m in multi_group.members] == [8, 9]
        assert multi_group.eligible_market_value_eur == Decimal("250.00")
        assert multi_group.eligible_cost_basis_eur == Decimal("200.00")
        assert multi_group.gain_eur == Decimal("50.00")
        assert multi_group.return_percent == Decimal("25")

        # Group 4 (Rows 10 + 11, SYNTH-PARTIAL-COST): Row 11 lacks cost -> group return_percent is None!
        partial_cost_group = returns.groups[4]
        assert [m.valuation.position.source_row for m in partial_cost_group.members] == [10, 11]
        assert partial_cost_group.eligible_market_value_eur == Decimal("80.00")
        assert partial_cost_group.eligible_cost_basis_eur == Decimal("50.00")
        assert partial_cost_group.return_percent is None

    def test_unverified_cost_basis_blocks_all_personal_returns_without_affecting_valuation(
        self,
    ) -> None:
        _, valuation, returns, _ = self._run_pipeline(verify_cost_basis=False)

        assert valuation.total_eur == Decimal("1095.00")
        assert returns.total.eligible_count == 0
        assert returns.total.eligible_market_value_eur is None
        assert returns.total.eligible_cost_basis_eur is None
        assert returns.total.return_percent is None
        assert all(group.return_percent is None for group in returns.groups)
        personal_treemap = build_treemap(valuation, personal_performances(returns))
        assert [t.amount_eur for t in personal_treemap.tiles] == [
            Decimal("120.00"),
            Decimal("330.0000"),
            Decimal("150.000"),
            Decimal("250.00"),
            Decimal("200.00"),
            Decimal("45.00"),
        ]
        assert all(
            t.percent is None and t.color == MISSING_COLOR and t.label[-1] == "n. v."
            for t in personal_treemap.tiles
        )

    def test_price_periods_splits_gaps_and_treemap_consistency_across_all_views(
        self,
    ) -> None:
        _, valuation, returns, _ = self._run_pipeline(verify_cost_basis=True)

        # Build deterministic exchange calendars for 2025-01-01..2026-12-31.
        start_day, end_day = date(2025, 1, 1), date(2026, 12, 31)
        weekdays = tuple(
            start_day + timedelta(days=n)
            for n in range((end_day - start_day).days + 1)
            if (start_day + timedelta(days=n)).weekday() < 5
        )
        xetra_cal = session_calendar(weekdays, time(9, 0), time(17, 30), start_day, end_day)
        nyse_cal = session_calendar(weekdays, time(9, 30), time(16, 0), start_day, end_day)
        lse_cal = session_calendar(weekdays, time(8, 0), time(16, 30), start_day, end_day)

        def make_history(
            cal,
            unit: QuoteUnit,
            tz: str,
            overrides: dict[date, str],
            *,
            first_bar: date = start_day,
            skip_days: set[date] = frozenset(),
            split_day: date | None = None,
        ) -> PriceHistory:
            points = tuple(
                HistoryPoint(
                    d,
                    Decimal(overrides.get(d, "100")),
                    Decimal("2") if d == split_day else Decimal("0"),
                )
                for d in sorted(cal.sessions)
                if d >= first_bar and d not in skip_days
            )
            return PriceHistory(
                ValueStatus.AVAILABLE,
                Provenance(ValuationSource.PROVIDER, EVAL_NOW, None, "synthetic"),
                unit,
                tz,
                BASIS,
                points,
            )

        # 1. EUR Alpha (XETRA closed at 18:00 UTC = 20:00 CEST on 2026-10-23)
        h_alpha = make_history(
            xetra_cal,
            EUR_UNIT,
            "Europe/Berlin",
            {
                date(2025, 10, 23): "8.00",
                date(2026, 9, 23): "10.00",
                date(2026, 10, 16): "10.00",
                date(2026, 10, 22): "10.00",
                date(2026, 10, 23): "12.00",
            },
        )
        p_alpha = price_period_returns(h_alpha, None, xetra_cal, EVAL_NOW)
        assert p_alpha[PricePeriod.TODAY].percent == Decimal("20")
        assert p_alpha[PricePeriod.WEEK].percent == Decimal("20")
        assert p_alpha[PricePeriod.MONTH].percent == Decimal("20")
        assert p_alpha[PricePeriod.YEAR].percent == Decimal("50")

        # 2. USD Beta (NYSE open at 18:00 UTC = 14:00 EDT on 2026-10-23; 2:1 split on 2026-10-20)
        # Split-adjusted closes are all 50.00 USD before and after the split, live quote is 52.50 USD (+5 %).
        h_beta = make_history(
            nyse_cal,
            USD_UNIT,
            "America/New_York",
            {
                date(2025, 10, 23): "40.00",
                date(2026, 9, 23): "50.00",
                date(2026, 10, 16): "50.00",
                date(2026, 10, 22): "50.00",
                date(2026, 10, 23): "1.00",  # Incomplete intraday bar must be ignored!
            },
            split_day=date(2026, 10, 20),
        )
        live_beta = PriceObservation(
            Valuation(
                Decimal("52.50"),
                ValueStatus.AVAILABLE,
                Provenance(
                    ValuationSource.PROVIDER,
                    EVAL_NOW,
                    datetime(2026, 10, 23, 13, 55, tzinfo=ZoneInfo("America/New_York")),
                    "synthetic",
                ),
                USD_UNIT,
            ),
            BASIS,
        )
        p_beta = price_period_returns(h_beta, live_beta, nyse_cal, EVAL_NOW)
        assert p_beta[PricePeriod.TODAY].market_open is True
        assert p_beta[PricePeriod.TODAY].percent == Decimal("5")
        assert p_beta[PricePeriod.WEEK].percent == Decimal("5")  # No artificial split jump!
        assert p_beta[PricePeriod.YEAR].percent == Decimal("31.25")

        # 3. UK ETF in GBp (newly listed on 2026-10-01 -> Today & Week available in GBp, Month & Year n. v.)
        h_gbx = make_history(
            lse_cal,
            GBX_UNIT,
            "Europe/London",
            {
                date(2026, 10, 16): "200",
                date(2026, 10, 22): "240",
                date(2026, 10, 23): "252",
            },
            first_bar=date(2026, 10, 1),
        )
        p_gbx = price_period_returns(h_gbx, None, lse_cal, EVAL_NOW)
        assert p_gbx[PricePeriod.TODAY].percent == Decimal("5")
        assert p_gbx[PricePeriod.TODAY].unit == GBX_UNIT
        assert p_gbx[PricePeriod.WEEK].percent == Decimal("26")
        assert p_gbx[PricePeriod.MONTH].percent is None
        assert p_gbx[PricePeriod.MONTH].reason == "insufficient_history"
        assert p_gbx[PricePeriod.YEAR].percent is None
        assert p_gbx[PricePeriod.YEAR].reason == "insufficient_history"

        # 4. Multi-Hold (XETRA, steady +10 % across all periods)
        h_multi = make_history(
            xetra_cal,
            EUR_UNIT,
            "Europe/Berlin",
            {date(2026, 10, 23): "110"},
        )
        p_multi = price_period_returns(h_multi, None, xetra_cal, EVAL_NOW)
        assert all(p_multi[period].percent == Decimal("10") for period in PricePeriod)

        # 5. Partial-Cost (unexpected 3-session gap around 2026-10-16 -> WEEK is unexpected_gap)
        h_gap = make_history(
            xetra_cal,
            EUR_UNIT,
            "Europe/Berlin",
            {date(2026, 10, 23): "105"},
            skip_days={date(2026, 10, 16), date(2026, 10, 15), date(2026, 10, 14)},
        )
        p_gap = price_period_returns(h_gap, None, xetra_cal, EVAL_NOW)
        assert p_gap[PricePeriod.TODAY].percent == Decimal("5")
        assert p_gap[PricePeriod.WEEK].percent is None
        assert p_gap[PricePeriod.WEEK].reason == "unexpected_gap"

        # 6..9: Groups without period history or with Sheet fallback (Row 12 derivative, Rows 13..16)
        unavailable_history = PriceHistory(
            ValueStatus.MISSING,
            Provenance(ValuationSource.PROVIDER, EVAL_NOW, None, "synthetic"),
        )
        p_none = price_period_returns(unavailable_history, None, xetra_cal, EVAL_NOW)

        group_periods = [
            p_alpha,
            p_beta,
            p_gbx,
            p_multi,
            p_gap,
            p_none,  # Derivative with Sheet price -> supports_period_return is False
            p_none,  # Missing price
            p_none,  # Missing FX
            p_none,  # Zero price
            p_none,  # Negative position
        ]
        assert len(group_periods) == len(valuation.groups) == 10

        expected_tile_amounts = [
            Decimal("120.00"),
            Decimal("330.0000"),
            Decimal("150.000"),
            Decimal("250.00"),
            Decimal("200.00"),
            Decimal("45.00"),
        ]

        # Verify Treemap consistency across all 4 course periods + Personal view:
        for period in PricePeriod:
            perfs = [
                performance_from_period(gp[period])
                if any(
                    m.amount_eur is not None
                    and m.position.valuation is not None
                    and supports_period_return(m.position.valuation)
                    for m in group.members
                )
                else Performance(None)
                for group, gp in zip(valuation.groups, group_periods)
            ]
            model = build_treemap(valuation, perfs)
            assert [t.amount_eur for t in model.tiles] == expected_tile_amounts
            assert sum((t.amount_eur for t in model.tiles), Decimal("0")) == valuation.total_eur
            assert model.omitted_count == 4  # Missing price, missing FX, 0 EUR warrant, negative row
            # Sheet derivative tile (index 5) is always gray n. v. with unknown quote time.
            deriv_tile = model.tiles[5]
            assert deriv_tile.percent is None
            assert deriv_tile.color == MISSING_COLOR
            assert deriv_tile.label[-1] == "n. v."
            assert "Datenstand: Kursstand unbekannt" in deriv_tile.tooltip

        personal_model = build_treemap(valuation, personal_performances(returns))
        assert [t.amount_eur for t in personal_model.tiles] == expected_tile_amounts
        assert sum((t.amount_eur for t in personal_model.tiles), Decimal("0")) == valuation.total_eur
        assert personal_model.omitted_count == 4
        assert [t.label[-1] for t in personal_model.tiles] == [
            "+20,00 %",
            "+10,00 %",
            "+25,00 %",
            "+25,00 %",
            "n. v.",  # Partial-Cost group has 1 of 2 rows without cost basis -> n. v.
            "n. v.",  # Derivative without cost basis -> n. v.
        ]

    def test_numerical_tolerances_and_rounding_invariants(self) -> None:
        _, valuation, returns, _ = self._run_pipeline(verify_cost_basis=True)

        # 1. Exact EUR amounts and subtotals (zero tolerance):
        valued_amounts = [
            pv.amount_eur for pv in valuation.positions if pv.amount_eur is not None
        ]
        assert sum(valued_amounts, Decimal("0")) == valuation.total_eur == Decimal("1095.0000")

        group_amounts = [
            g.amount_eur for g in valuation.groups if g.amount_eur is not None
        ]
        assert sum(group_amounts, Decimal("0")) == valuation.total_eur

        # 2. Weights sum to 1 within <= n * 10^(-prec) (1 ULP per term at prec=28):
        pos_weights = [
            pv.weight_fraction
            for pv in valuation.positions
            if pv.weight_fraction is not None
        ]
        group_weights = [
            g.weight_fraction
            for g in valuation.groups
            if g.weight_fraction is not None
        ]
        assert abs(sum(pos_weights, Decimal("0")) - Decimal("1")) <= Decimal("1e-27")
        assert abs(sum(group_weights, Decimal("0")) - Decimal("1")) <= Decimal("1e-27")

        # 3. Caller precision invariance for exact EUR sums/gains and 1-ULP bound for ratios:
        with localcontext() as ctx:
            ctx.prec = 10
            low_prec_returns = portfolio_returns(valuation)
        assert (
            low_prec_returns.total.eligible_market_value_eur
            == returns.total.eligible_market_value_eur
        )
        assert (
            low_prec_returns.total.eligible_cost_basis_eur
            == returns.total.eligible_cost_basis_eur
        )
        assert low_prec_returns.total.gain_eur == returns.total.gain_eur
        assert abs(
            low_prec_returns.total.return_percent - returns.total.return_percent
        ) <= Decimal("1e-7")


def test_domain_regression_pipeline_runs_offline_without_ui_or_network() -> None:
    """Verify that the integrated domain pipeline imports no UI/network modules and opens no sockets."""
    code = """
import importlib.abc
import socket
import sys

class OfflineGuard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {'streamlit', 'plotly', 'yfinance', 'requests', 'gspread'}:
            raise AssertionError(f'Forbidden module imported: {fullname}')

sys.meta_path.insert(0, OfflineGuard())

def deny_socket(*args, **kwargs):
    raise AssertionError('Socket access attempted')

socket.socket = deny_socket
socket.create_connection = deny_socket

from datetime import UTC, datetime
from portfolio_dashboard.sheet_parser import NumberLocale, SHEET_HEADERS, SheetMetadata, parse_sheet_rows
from portfolio_dashboard.valuations import value_portfolio
from portfolio_dashboard.returns import portfolio_returns
from portfolio_dashboard.treemap import build_treemap, personal_performances

parsed = parse_sheet_rows(
    SHEET_HEADERS,
    [
        [
            'Synthetische Aktie', 'SYN', 'Aktie', '2', 'FRA:SYN', 'EUR',
            '15,00', '30,00', '20,00', '50', '10,00', 'Kern', '100',
        ]
    ],
    imported_at=datetime(2026, 10, 23, 12, 0, tzinfo=UTC),
    metadata=SheetMetadata(NumberLocale.GERMAN, cost_basis_verified_remaining=True),
)
valuation = value_portfolio(parsed.snapshot, ())
returns = portfolio_returns(valuation)
treemap = build_treemap(valuation, personal_performances(returns))
assert len(treemap.tiles) == 1
assert treemap.tiles[0].label[-1] == '+50,00 %'
"""
    subprocess.run([sys.executable, "-c", code], check=True)
