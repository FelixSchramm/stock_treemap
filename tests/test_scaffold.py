"""Verify the offline domain boundary and the actual Streamlit entry point."""

import subprocess
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_domain_import_and_demo_without_ui_or_network():
    # A new interpreter cannot hide accidental UI imports in its module cache.
    code = """
import importlib.abc
import socket
import sys
class NoUI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {'streamlit', 'pandas', 'plotly', 'yfinance'}:
            raise AssertionError(f'Unexpected dependency: {fullname}')
sys.meta_path.insert(0, NoUI())
def no_network(*args, **kwargs):
    raise AssertionError('Domain code attempted a network connection')
socket.socket = no_network
from portfolio_dashboard.demo import demo_rows
from portfolio_dashboard.config import load_config
from portfolio_dashboard.fixtures import synthetic_import_cases
assert load_config({}).mode.value == 'demo'
assert len(synthetic_import_cases()) >= 6
first = demo_rows()
assert len(first) == 2
assert first[0]['Instrument'] == 'Demo-Aktie A'
first[0]['Instrument'] = 'changed'
assert demo_rows()[0]['Instrument'] == 'Demo-Aktie A'
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_streamlit_entry_point_shows_synthetic_demo(monkeypatch):
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("Demo attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", no_network)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py")).run()
    assert not app.exception
    assert app.title[0].value == "Depot-Dashboard – Demo"
    assert "synthetische" in app.info[0].value
    table = app.dataframe[0].value
    assert table["Instrument"].tolist() == ["Demo-Aktie A", "Demo-Fonds B"]
    assert table["Wert (EUR)"].tolist() == [1200.0, 800.0]
