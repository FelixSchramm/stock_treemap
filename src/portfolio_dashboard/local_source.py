"""Sheet-only refresh coordinator for the loopback-bound local mode.

Values, weights and returns since purchase come from the owner's Sheet. Without
verified listings the coordinator never requests provider quotes or histories.
"""

from portfolio_dashboard.config import AppConfig
from portfolio_dashboard.market_data import YFinanceProvider
from portfolio_dashboard.quote_cache import QuoteCache
from portfolio_dashboard.refresh import PortfolioRefreshCoordinator
from portfolio_dashboard.sheet_parser import NumberLocale, SheetMetadata
from portfolio_dashboard.sheets_adapter import SheetsAdapter
from portfolio_dashboard.sheets_session import build_readonly_session

# Owner decisions: German Sheet; column I holds the cost of remaining units (#20).
LOCAL_SHEET_METADATA = SheetMetadata(
    NumberLocale.GERMAN, cost_basis_verified_remaining=True
)


def build_local_coordinator(config: AppConfig) -> PortfolioRefreshCoordinator:
    """Read the configured Sheet through the read-only service-account session."""
    adapter = SheetsAdapter(
        config.source,
        LOCAL_SHEET_METADATA,
        build_readonly_session(config.source.credentials_file),
    )
    return PortfolioRefreshCoordinator(
        adapter.read, QuoteCache(YFinanceProvider(()), config.ttls)
    )
