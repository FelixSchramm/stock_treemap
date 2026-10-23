"""Streamlit entry point for the offline starter demo."""

import streamlit as st

from portfolio_dashboard.config import ConfigurationError, Mode, load_config
from portfolio_dashboard.demo import demo_rows

st.set_page_config(page_title="Depot-Dashboard – Demo")
try:
    config = load_config()
except ConfigurationError as exc:
    st.error(str(exc))
    st.stop()

if config.mode is Mode.PRODUCTION:
    st.error("Produktivzugang ist noch nicht freigeschaltet. Keine Depotdaten geladen.")
    st.stop()

st.title("Depot-Dashboard – Demo")
st.info("Nur synthetische Beispieldaten. Kein Zugriff auf echte Depot- oder Kursdaten.")
st.dataframe(demo_rows(), hide_index=True)
