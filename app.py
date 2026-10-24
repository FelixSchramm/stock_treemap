"""Streamlit entry point for the offline starter demo."""

import streamlit as st

from portfolio_dashboard.demo import demo_rows

st.set_page_config(page_title="Depot-Dashboard – Demo")
st.title("Depot-Dashboard – Demo")
st.info("Nur synthetische Beispieldaten. Kein Zugriff auf echte Depot- oder Kursdaten.")
st.dataframe(demo_rows(), hide_index=True)
