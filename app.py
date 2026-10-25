"""Streamlit entry point; real data stays closed until owner authorization.

Local mode has no login and therefore serves the owner's Sheet on loopback only.
"""

import os
from datetime import UTC, datetime

import streamlit as st

from portfolio_dashboard.auth import require_owner
from portfolio_dashboard.config import ConfigurationError, Mode, load_config
from portfolio_dashboard.demo import (
    demo_course_performances,
    demo_personal_performances,
    demo_rows,
    demo_snapshot,
    demo_valuation,
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

LOOPBACK_ADDRESSES = {"127.0.0.1", "localhost", "::1"}
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


def render_own_portfolio(build_coordinator) -> None:
    """Build the owner's coordinator once per session, then render and stop."""
    if OWN_COORDINATOR_KEY not in st.session_state:
        try:
            st.session_state[OWN_COORDINATOR_KEY] = build_coordinator()
        except (ListingsFileError, SheetsConfigurationError, SheetsCredentialError) as exc:
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
    )
    st.stop()


if config.mode is Mode.LOCAL:
    # Without login, the owner's data must never be reachable from the network.
    # Local mode never reads st.secrets, so hosted secrets cannot feed it.
    if st.get_option("server.address") not in LOOPBACK_ADDRESSES:
        st.error("Lokaler Modus nur mit --server.address 127.0.0.1.")
        st.stop()
    render_own_portfolio(lambda: build_local_coordinator(config))

if config.mode is Mode.PRODUCTION:
    # Every run passes the owner gate before secrets, loaders and output.
    # Never cache an authorization decision.
    require_owner(config.login)
    render_own_portfolio(
        lambda: build_production_coordinator(
            config,
            st.secrets.get("sheets_service_account"),
            st.secrets.get("listing", []),
        )
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
)
