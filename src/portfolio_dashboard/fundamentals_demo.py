"""Synthetic fundamentals for the demo mode (M8-05, #74).

Everything here is invented and offline: a fixed clock, an in-memory store that
never writes a file, and a fake provider that returns round invented statements.
The loader code path is the real one, so a view behaves the same in demo and in
real operation. Demo never reaches the protected cache, the real provider or any
real identity.
"""

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

from portfolio_dashboard.fundamentals import (
    EvidenceKind,
    FieldProvenance,
    FlowPeriod,
    FundamentalField,
    FundamentalReportSet,
    FundamentalSource,
    IssuerKind,
    ManualAdjustment,
    PeriodKind,
    VerifiedFundamentalIdentity,
)
from portfolio_dashboard.fundamentals_cache import MemoryFundamentalsStore
from portfolio_dashboard.fundamentals_config import (
    FundamentalSubject,
    FundamentalSubjects,
)
from portfolio_dashboard.fundamentals_loader import FetchGate, FundamentalsLoader
from portfolio_dashboard.fundamentals_normalization import (
    OwnerConfirmations,
    ProviderExpectation,
    ProviderStatements,
    StatementTable,
)
from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.intrinsic_models import (
    ForecastYear,
    InputReference,
    Scenario,
    ScenarioAssumptions,
    TerminalPlan,
)
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    ProviderMapping,
    QuoteUnit,
    ValueStatus,
)
from portfolio_dashboard.scenarios import (
    ManualPriority,
    ManualPurpose,
    ManualValue,
    ScenarioDocument,
)

DEMO_FUNDAMENTALS_TIMESTAMP = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
DEMO_FUNDAMENTALS_INSTRUMENT = Instrument(
    InstrumentType.STOCK,
    instrument_id="DEMO-FUNDAMENTALS-A",
    trading_venue="DEMO",
    quote_unit=QuoteUnit("EUR", Decimal(1)),
    provider_mapping=ProviderMapping("yfinance", "DEMO-A.SYN"),
)
_YEARS = tuple(date(year, 12, 31) for year in (2025, 2024, 2023, 2022))
_BALANCE_DATES = (*_YEARS, date(2021, 12, 31))


def _table(rows: dict[str, tuple[int, ...]], ends: tuple[date, ...]) -> StatementTable:
    return StatementTable(
        {
            label: {end: Decimal(value) for end, value in zip(ends, values)}
            for label, values in rows.items()
        }
    )


def demo_statements(
    retrieved_at: datetime = DEMO_FUNDAMENTALS_TIMESTAMP,
    trading_currency: str = "EUR",
) -> ProviderStatements:
    """Invented annual statements of a fictitious company with a debt identity."""
    million = 1_000_000
    return ProviderStatements(
        retrieved_at,
        annual_income=_table(
            {
                "Operating Income": (
                    150 * million,
                    140 * million,
                    130 * million,
                    120 * million,
                ),
                "Pretax Income": (
                    140 * million,
                    130 * million,
                    120 * million,
                    110 * million,
                ),
                "Tax Provision": (
                    28 * million,
                    26 * million,
                    24 * million,
                    22 * million,
                ),
                "Net Income Common Stockholders": (
                    100 * million,
                    95 * million,
                    90 * million,
                    85 * million,
                ),
                "Diluted Average Shares": (10 * million,) * 4,
            },
            _YEARS,
        ),
        annual_balance=_table(
            {
                "Current Assets": tuple(m * million for m in (300, 290, 280, 270, 260)),
                "Current Liabilities": tuple(
                    m * million for m in (200, 195, 190, 185, 180)
                ),
                "Cash Cash Equivalents And Short Term Investments": (100 * million,)
                * 5,
                "Current Debt And Capital Lease Obligation": (50 * million,) * 5,
                "Long Term Debt And Capital Lease Obligation": (70 * million,) * 5,
                "Total Debt": (120 * million,) * 5,
                "Ordinary Shares Number": (10 * million,) * 5,
            },
            _BALANCE_DATES,
        ),
        annual_cashflow=_table(
            {
                "Depreciation And Amortization": (20 * million,) * 4,
                "Capital Expenditure": (-44 * million,) * 4,
                "Operating Cash Flow": (100 * million,) * 4,
            },
            _YEARS,
        ),
        info={
            "quoteType": "EQUITY",
            "currency": trading_currency,
            "financialCurrency": "EUR",
            "sharesOutstanding": 10 * million,
            "impliedSharesOutstanding": None,
        },
        dated_shares=((date(2026, 9, 30), Decimal(10 * million)),),
    )


class SyntheticFundamentalsProvider:
    """Offline stand-in for the adapter; ``fetch`` returns the invented statements."""

    def fetch(self, identity: VerifiedFundamentalIdentity) -> ProviderStatements:
        return demo_statements(
            trading_currency=identity.listing.instrument.quote_unit.currency
        )


def _stock(
    instrument_id: str,
    venue: str,
    symbol: str,
    currency: str,
    *,
    suffix: str,
    kind: IssuerKind = IssuerKind.NONFINANCIAL,
    confirmed: bool = False,
    source_symbol: str | None = None,
) -> FundamentalSubject:
    """One invented verified listing with its fundamentals identity."""
    instrument = Instrument(
        InstrumentType.STOCK,
        instrument_id=instrument_id,
        trading_venue=venue,
        quote_unit=QuoteUnit(currency, Decimal(1)),
        provider_mapping=ProviderMapping("yfinance", symbol),
    )
    identity = VerifiedFundamentalIdentity(
        VerifiedListing(instrument, equity_form=EquityForm.SHARE, isin=instrument_id),
        f"demo-class-{suffix}",
        f"demo-issuer-{suffix}",
        FundamentalSource("yfinance", source_symbol or symbol),
        f"demo-group-{suffix}",
        kind,
        "demo-evidence",
        DEMO_FUNDAMENTALS_TIMESTAMP,
    )
    return FundamentalSubject(
        identity,
        ProviderExpectation("EUR", 12, 31),
        1,
        "demo-class-evidence",
        DEMO_FUNDAMENTALS_TIMESTAMP,
        OwnerConfirmations(confirmed, confirmed, confirmed),
    )


def _original_subject() -> FundamentalSubject:
    identity = VerifiedFundamentalIdentity(
        VerifiedListing(
            DEMO_FUNDAMENTALS_INSTRUMENT,
            equity_form=EquityForm.SHARE,
            isin="DEMO-FUNDAMENTALS-A",
        ),
        "demo-class",
        "demo-issuer",
        FundamentalSource("yfinance", "DEMO-A.SYN"),
        "demo-group",
        IssuerKind.NONFINANCIAL,
        "demo-evidence",
        DEMO_FUNDAMENTALS_TIMESTAMP,
    )
    return FundamentalSubject(
        identity,
        ProviderExpectation("EUR", 12, 31),
        1,
        "demo-class-evidence",
        DEMO_FUNDAMENTALS_TIMESTAMP,
    )


# The valued demo security is listed on two venues (Frankfurt for the demo
# portfolio, Xetra for the Watchlist row) with one shared source: one fetch, one
# set of model values. The others show the states of the overview.
DEMO_VALUED_ID = "DEMO-FUNDAMENTALS-E"
DEMO_ASSUMPTIONS_MISSING_ID = "DEMO-FUNDAMENTALS-C"
DEMO_BANK_ID = "DEMO-FUNDAMENTALS-D"
DEMO_ETF_ID = "DEMO-ETF-B"


def demo_subjects() -> FundamentalSubjects:
    """Invented identities: the original one plus the overview's demo securities."""
    return FundamentalSubjects(
        (
            _original_subject(),
            _stock(
                DEMO_VALUED_ID,
                "FRA",
                "DEMOA.F",
                "EUR",
                suffix="e",
                confirmed=True,
                source_symbol="DEMO-E.SYN",
            ),
            _stock(
                DEMO_VALUED_ID,
                "GER",
                "DEMOA.DE",
                "EUR",
                suffix="e",
                confirmed=True,
                source_symbol="DEMO-E.SYN",
            ),
            _stock(DEMO_ASSUMPTIONS_MISSING_ID, "NMS", "DEMOC", "USD", suffix="c"),
            _stock(
                DEMO_BANK_ID, "NYQ", "DEMOD", "USD", suffix="d", kind=IssuerKind.BANK
            ),
        )
    )


def demo_registry() -> tuple[VerifiedListing, ...]:
    """Verified listings of the demo: the subjects' listings plus one verified ETF."""
    etf = VerifiedListing(
        Instrument(
            InstrumentType.ETF,
            instrument_id=DEMO_ETF_ID,
            trading_venue="FRA",
            quote_unit=QuoteUnit("EUR", Decimal(1)),
            provider_mapping=ProviderMapping("yfinance", "DEMOB.F"),
        ),
        isin=DEMO_ETF_ID,
        share_class="demo-share-class",
    )
    return (*(s.identity.listing for s in demo_subjects().subjects), etf)


def demo_fundamentals_loader() -> FundamentalsLoader:
    """Same ``load`` interface as the real loader, fixed clock, no sleeping, no IO."""
    return FundamentalsLoader(
        demo_subjects(),
        SyntheticFundamentalsProvider(),
        MemoryFundamentalsStore(),
        clock=lambda: DEMO_FUNDAMENTALS_TIMESTAMP,
        sleep=lambda seconds: None,
        package_version="demo",
        gate=FetchGate(),
    )


# --- invented owner input: manual values and three confirmed scenarios -------------

_DEMO_PLANS = {  # WACC, g, NOPAT_6 and capex_6 in millions (INTRINSIC_VALUE.md).
    Scenario.CAUTIOUS: ("0.12", "0.01", "161.60", "36.16"),
    Scenario.BASE: ("0.10", "0.02", "163.20", "52.64"),
    Scenario.OPTIMISTIC: ("0.08", "0.03", "164.80", "69.44"),
}
_MANUAL_VALUES = {  # field -> invented value; all synthetic, none from a source
    FundamentalField.OPERATING_TAX_RATE: Decimal("0.20"),
    FundamentalField.EXCESS_CASH: Decimal(100_000_000),
    FundamentalField.NONOPERATING_ASSETS: Decimal(0),
    FundamentalField.PREFERRED_CLAIMS: Decimal(0),
    FundamentalField.MINORITY_CLAIMS: Decimal(0),
    FundamentalField.INCREMENTAL_DILUTION: Decimal(0),
}
DEMO_ASSUMPTIONS_VERSION = "demo-assumptions-v1"
DEMO_DATA_VERSION = "demo-manual-v1"
DEMO_MODEL_VERSION = "fcff-dcf-v1"


def _year(year: int) -> FlowPeriod:
    return FlowPeriod(
        date(year, 1, 1),
        date(year, 12, 31),
        PeriodKind.FISCAL_YEAR,
        f"FY ending {year}-12-31",
        True,
    )


def demo_scenario_document(originals: FundamentalReportSet) -> ScenarioDocument:
    """Invented owner input for the demo report set: manual values, three plans.

    Every gap that the provider leaves open is filled with an invented number, each
    as an explicit manual value with its original kept; the three confirmations
    carry an invented reference. Nothing here is a real assumption.
    """
    at = DEMO_FUNDAMENTALS_TIMESTAMP
    reference = InputReference.from_report_set(originals)
    manual_values = []
    for original in originals.observations:
        value = _MANUAL_VALUES.get(original.field)
        if value is None or original.status is ValueStatus.AVAILABLE:
            continue
        replacement = replace(
            original,
            value=value,
            status=ValueStatus.AVAILABLE,
            provenance=FieldProvenance(
                originals.identity.source,
                EvidenceKind.MANUAL,
                "demo-synthetic",
                "invented demo value",
                "invented demo value, no source",
                None,
            ),
        )
        adjustment = ManualAdjustment(
            original, replacement, "invented demo input", at, at, "demo-confirmation"
        )
        manual_values.append(
            ManualValue(
                reference,
                adjustment,
                ManualPurpose.SUPPLEMENT,
                ManualPriority.MANUAL_WHEN_CONFIRMED,
            )
        )
    document = ScenarioDocument(
        originals,
        DEMO_DATA_VERSION,
        DEMO_MODEL_VERSION,
        at,
        tuple(manual_values),
    )
    million = Decimal(1_000_000)
    for scenario, (wacc, growth, nopat6, capex6) in _DEMO_PLANS.items():
        years = tuple(
            ForecastYear(
                n,
                _year(2026 + n),
                Decimal(160 + 10 * (n - 1)) * million,
                Decimal("0.20"),
                20 * million,
                48 * million,
                20 * million,
            )
            for n in range(1, 6)
        )
        assumptions = ScenarioAssumptions(
            document.inputs,
            DEMO_ASSUMPTIONS_VERSION,
            scenario,
            at,
            "EUR",
            years,
            TerminalPlan(
                Decimal(nopat6) * million,
                20 * million,
                Decimal(capex6) * million,
                Decimal(0),
                "invented sustainable year",
            ),
            Decimal(wacc),
            Decimal(growth),
            Decimal("0.10"),
            Decimal("0.20") if scenario is Scenario.BASE else None,
            "invented demo plan",
            at,
            "demo-confirmation",
        )
        document = document.with_scenario(assumptions, changed_at=at)
    return document


class DemoScenarioSource:
    """Invented scenario document in a temporary store; nothing real, nothing durable.

    ``repository`` is the shared ``ScenarioRepository`` of the demo session (a
    temporary database that belongs to the session). The first read seeds the
    invented document of the valued demo security as a normal saved revision, so
    the detail view edits, saves and detects conflicts exactly as in real operation.
    """

    def __init__(self, loader: FundamentalsLoader, repository) -> None:
        self._loader = loader
        self._repository = repository

    def get(self, identity: VerifiedFundamentalIdentity) -> ScenarioDocument | None:
        stored = self._repository.get(identity)
        if (
            stored is not None
            or identity.listing.instrument.instrument_id != DEMO_VALUED_ID
        ):
            return stored
        entry = self._loader.load([identity.listing.instrument]).entry(
            identity.listing.instrument
        )
        if entry.report_set is None:
            return None
        return self._repository.save(demo_scenario_document(entry.report_set))

    def save(self, document: ScenarioDocument) -> ScenarioDocument:
        return self._repository.save(document)
