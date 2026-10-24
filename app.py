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
from portfolio_dashboard.local_source import build_local_coordinator
from portfolio_dashboard.models import PortfolioSnapshot, SnapshotStatus
from portfolio_dashboard.sheets_adapter import SheetsConfigurationError
from portfolio_dashboard.sheets_session import SheetsCredentialError
from portfolio_dashboard.ui import COURSE_VIEW, render_dashboard
from portfolio_dashboard.valuations import value_portfolio

LOOPBACK_ADDRESSES = {"127.0.0.1", "localhost", "::1"}
LOCAL_COORDINATOR_KEY = "local.coordinator"

mode_setting = os.environ.get("PORTFOLIO_MODE", "demo").strip()
st.set_page_config(
    page_title="Depot-Dashboard" if mode_setting == Mode.LOCAL else "Depot-Dashboard – Demo",
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

if config.mode is Mode.LOCAL:
    # Without login, the owner's data must never be reachable from the network.
    if st.get_option("server.address") not in LOOPBACK_ADDRESSES:
        st.error("Lokaler Modus nur mit --server.address 127.0.0.1.")
        st.stop()
    if LOCAL_COORDINATOR_KEY not in st.session_state:
        try:
            st.session_state[LOCAL_COORDINATOR_KEY] = build_local_coordinator(config)
        except (SheetsConfigurationError, SheetsCredentialError) as exc:
            st.error(str(exc))
            st.stop()
    # The coordinator replaces these placeholders with its atomic bundle.
    empty = PortfolioSnapshot(datetime.now(UTC), SnapshotStatus.COMPLETE)
    render_dashboard(
        empty,
        None,
        value_portfolio(empty, ()),
        lambda view, period: (),
        coordinator=st.session_state[LOCAL_COORDINATOR_KEY],
        demo=False,
    )
    st.stop()

if config.mode is Mode.PRODUCTION:
    require_owner(config.login)
    st.info("Besitzerzugriff freigegeben. Echte Daten sind noch nicht verfügbar; es folgt nur die Demo.")

# All loaders and output (including future real-data/export paths) belong below
# the production owner gate. Never cache an authorization decision.
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
