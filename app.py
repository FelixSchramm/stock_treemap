"""Streamlit entry point; real data stays closed until owner authorization.

Local mode has no login and therefore serves the owner's Sheet on loopback only.
"""

import os
from datetime import UTC, datetime

import streamlit as st

from portfolio_dashboard.auth import require_owner
from portfolio_dashboard.config import (
    LOOPBACK_ADDRESSES,
    ConfigurationError,
    Mode,
    load_config,
)
from portfolio_dashboard.demo import (
    DemoWatchlist,
    demo_course_performances,
    demo_personal_performances,
    demo_rows,
    demo_snapshot,
    demo_valuation,
)
from portfolio_dashboard.fundamentals_config import FundamentalsConfigError
from portfolio_dashboard.fundamentals_loader import (
    LOADER_STATE_KEY as FUNDAMENTALS_LOADER_KEY,
)
from portfolio_dashboard.fundamentals_loader import (
    build_local_fundamentals_loader,
    build_production_fundamentals_loader,
)
from portfolio_dashboard.local_source import (
    ListingsFileError,
    build_local_coordinator,
    build_production_coordinator,
)
from portfolio_dashboard.models import PortfolioSnapshot, SnapshotStatus
from portfolio_dashboard.sheets_adapter import SheetsConfigurationError
from portfolio_dashboard.sheets_session import SheetsCredentialError
from portfolio_dashboard.ui import COURSE_VIEW, render_dashboard
from portfolio_dashboard.valuations import value_portfolio
from portfolio_dashboard.watchlist_refresh import (
    build_local_watchlist_coordinator,
    build_production_watchlist_coordinator,
)

OWN_COORDINATOR_KEY = "own.coordinator"

mode_setting = os.environ.get("PORTFOLIO_MODE", "demo").strip()
st.set_page_config(
    page_title=(
        "Depot-Dashboard"
        if mode_setting in (Mode.LOCAL, Mode.PRODUCTION)
        else "Depot-Dashboard – Demo"
    ),
    layout="wide",
)
try:
    # Demo never accesses server auth secrets. Production must use the same
    # [auth] section as Streamlit's OIDC login and callback handlers.
    auth_settings = None
    if mode_setting == Mode.PRODUCTION:
        try:
            auth_settings = st.secrets.get("auth", {})
        except Exception:
            raise ConfigurationError("Produktivkonfiguration fehlt oder ist ungültig: auth.") from None
    config = load_config(auth_settings=auth_settings)
except ConfigurationError as exc:
    st.error(str(exc))
    st.stop()


def render_own_portfolio(build_coordinator, build_watchlist, build_fundamentals) -> None:
    """Build the owner's coordinator once per session, then render and stop.

    ``build_watchlist`` is only called by the Watchlist view (once per session and
    day), so no Watchlist loader exists before the owner gate and the first visit.
    The fundamentals loader (or ``None`` without a private mapping) is built once
    per session after the gate; building reads the mapping but performs no
    retrieval, and it only fetches when a view calls ``load`` (#74).
    """
    if OWN_COORDINATOR_KEY not in st.session_state:
        try:
            st.session_state[OWN_COORDINATOR_KEY] = build_coordinator()
        except (ListingsFileError, SheetsConfigurationError, SheetsCredentialError) as exc:
            st.error(str(exc))
            st.stop()
    if FUNDAMENTALS_LOADER_KEY not in st.session_state:
        try:
            st.session_state[FUNDAMENTALS_LOADER_KEY] = build_fundamentals()
        except FundamentalsConfigError as exc:
            st.error(str(exc))
            st.stop()
    # The coordinator replaces these placeholders with its atomic bundle.
    empty = PortfolioSnapshot(datetime.now(UTC), SnapshotStatus.COMPLETE)
    render_dashboard(
        empty,
        None,
        value_portfolio(empty, ()),
        lambda view, period: (),
        coordinator=st.session_state[OWN_COORDINATOR_KEY],
        demo=False,
        watchlist=build_watchlist,
    )
    st.stop()


if config.mode is Mode.LOCAL:
    # Without login, the owner's data must never be reachable from the network.
    # Local mode never reads st.secrets, so hosted secrets cannot feed it.
    if st.get_option("server.address") not in LOOPBACK_ADDRESSES:
        st.error("Lokaler Modus nur mit --server.address 127.0.0.1.")
        st.stop()
    render_own_portfolio(
        lambda: build_local_coordinator(config),
        lambda: build_local_watchlist_coordinator(config),
        lambda: build_local_fundamentals_loader(config),
    )

if config.mode is Mode.PRODUCTION:
    # Every run passes the owner gate before secrets, loaders and output.
    # Never cache an authorization decision.
    require_owner(config.login)
    render_own_portfolio(
        lambda: build_production_coordinator(
            config,
            st.secrets.get("sheets_service_account"),
            st.secrets.get("listing", []),
        ),
        lambda: build_production_watchlist_coordinator(
            config, st.secrets.get("sheets_service_account")
        ),
        lambda: build_production_fundamentals_loader(
            config, st.secrets.get("fundamental", [])
        ),
    )

# Only demo reaches this point: synthetic data without secrets or login.
demo_data = demo_snapshot()
demo_values = demo_valuation(demo_data)
render_dashboard(
    demo_data,
    demo_rows(),
    demo_values,
    lambda view, period: (
        demo_course_performances(period)
        if view == COURSE_VIEW
        else demo_personal_performances(demo_values)
    ),
    watchlist=DemoWatchlist,
)
