"""Streamlit entry point; real data stays closed until owner authorization."""

import streamlit as st

from portfolio_dashboard.config import ConfigurationError, Mode, load_config
from portfolio_dashboard.demo import demo_rows, demo_snapshot
from portfolio_dashboard.ui import render_dashboard

st.set_page_config(page_title="Depot-Dashboard – Demo", layout="wide")
try:
    config = load_config()
except ConfigurationError as exc:
    st.error(str(exc))
    st.stop()

if config.mode is Mode.PRODUCTION:
    st.error("Produktivzugang ist noch nicht freigeschaltet. Keine Depotdaten geladen.")
    st.stop()

render_dashboard(demo_snapshot(), demo_rows())
