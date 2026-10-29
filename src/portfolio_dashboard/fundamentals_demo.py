"""Synthetic fundamentals for the demo mode (M8-05, #74).

Everything here is invented and offline: a fixed clock, an in-memory store that
never writes a file, and a fake provider that returns round invented statements.
The loader code path is the real one, so a view behaves the same in demo and in
real operation. Demo never reaches the protected cache, the real provider or any
real identity.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from portfolio_dashboard.fundamentals import (
    FundamentalSource,
    IssuerKind,
    VerifiedFundamentalIdentity,
)
from portfolio_dashboard.fundamentals_cache import MemoryFundamentalsStore
from portfolio_dashboard.fundamentals_config import (
    FundamentalSubject,
    FundamentalSubjects,
)
from portfolio_dashboard.fundamentals_loader import FetchGate, FundamentalsLoader
from portfolio_dashboard.fundamentals_normalization import (
    ProviderExpectation,
    ProviderStatements,
    StatementTable,
)
from portfolio_dashboard.instruments import EquityForm, VerifiedListing
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    ProviderMapping,
    QuoteUnit,
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
            "currency": "EUR",
            "financialCurrency": "EUR",
            "sharesOutstanding": 10 * million,
            "impliedSharesOutstanding": None,
        },
        dated_shares=((date(2026, 9, 30), Decimal(10 * million)),),
    )


class SyntheticFundamentalsProvider:
    """Offline stand-in for the adapter; ``fetch`` returns the invented statements."""

    def fetch(self, identity: VerifiedFundamentalIdentity) -> ProviderStatements:
        return demo_statements()


def demo_subjects() -> FundamentalSubjects:
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
    return FundamentalSubjects(
        (
            FundamentalSubject(
                identity,
                ProviderExpectation("EUR", 12, 31),
                1,
                "demo-class-evidence",
                DEMO_FUNDAMENTALS_TIMESTAMP,
            ),
        )
    )


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
