"""Streamlit entry point; real data stays closed until owner authorization."""

import os

import streamlit as st

from portfolio_dashboard.auth import require_owner
from portfolio_dashboard.config import ConfigurationError, Mode, load_config
from portfolio_dashboard.demo import demo_rows, demo_snapshot
from portfolio_dashboard.ui import render_dashboard

st.set_page_config(page_title="Depot-Dashboard – Demo", layout="wide")
try:
    # Demo never accesses server auth secrets. Production must use the same
    # [auth] section as Streamlit's OIDC login and callback handlers.
    auth_settings = None
    if os.environ.get("PORTFOLIO_MODE", "demo").strip() == Mode.PRODUCTION:
        try:
            auth_settings = st.secrets.get("auth", {})
        except Exception:
            raise ConfigurationError("Produktivkonfiguration fehlt oder ist ungültig: auth.") from None
    config = load_config(auth_settings=auth_settings)
except ConfigurationError as exc:
    st.error(str(exc))
    st.stop()

if config.mode is Mode.PRODUCTION:
    require_owner(config.login)
    st.info("Besitzerzugriff freigegeben. Echte Daten sind noch nicht verfügbar; es folgt nur die Demo.")

# All loaders and output (including future real-data/export paths) belong below
# the production owner gate. Never cache an authorization decision.
render_dashboard(demo_snapshot(), demo_rows())
