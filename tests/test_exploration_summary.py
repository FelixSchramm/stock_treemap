"""Key figures and takeaways of the analysis page, with synthetic data only."""

import pandas as pd
import pytest

from portfolio_dashboard.exploration.charts import Analysis, build
from portfolio_dashboard.exploration.demo import demo_bundle
from portfolio_dashboard.exploration.selection import DEFAULT_COMPANIES
from portfolio_dashboard.exploration.summary import (
    NA,
    delta,
    headline,
    key_figures,
    number,
)


@pytest.fixture
def bundle():
    return demo_bundle()


def test_number_uses_german_separators_and_true_minus():
    assert number(1234.5) == "1.234,5"
    assert number(2, sign=True) == "+2,0"
    assert number(-0.25, 2) == "−0,25"
    # Streamlit derives the delta arrow and color from an ASCII minus.
    assert delta(-0.25, "Pp.") == "-0,2 Pp."
    assert delta(1.26, "%") == "+1,3 %"


def test_key_figures_match_topic_charts(bundle):
    figures = {f.label: f for f in key_figures(bundle)}
    assert list(figures) == [
        "Kurs in USD · 1 Jahr",
        "Abstand zum Hoch",
        "KGV",
        "FCF-Marge",
        "ROIC",
        "Dividendenrendite",
    ]
    close = bundle["prices"]["MSFT"].Close
    year = close.loc[close.index[-1] - pd.DateOffset(years=1) :]
    assert figures["Kurs in USD · 1 Jahr"].value == number(close.iloc[-1], 2)
    change = (year.iloc[-1] / year.iloc[0] - 1) * 100
    assert figures["Kurs in USD · 1 Jahr"].delta == delta(change, "%")
    assert len(figures["Kurs in USD · 1 Jahr"].chart) == len(year)
    pe = build(12, bundle).panels[0].data["KGV"].dropna().iloc[-1]
    assert figures["KGV"].value == f"{number(pe)} ×"
    margin = build(9, bundle).panels[1].data["Free Cashflow"].dropna()
    assert figures["FCF-Marge"].value == f"{number(margin.iloc[-1])} %"
    assert figures["FCF-Marge"].delta.endswith(" Pp.")
    roic = build(21, bundle).panels[0].data["ROIC"].dropna().iloc[-1]
    assert figures["ROIC"].value == f"{number(roic)} %"
    assert figures["Abstand zum Hoch"].value.endswith(" %")
    assert figures["Dividendenrendite"].value.endswith(" %")


@pytest.mark.parametrize("company", DEFAULT_COMPANIES[2:], ids=lambda c: c.symbol)
def test_pe_tile_follows_topic_rule_for_non_us_listings(company):
    figures = {f.label: f for f in key_figures(demo_bundle(company))}
    assert figures["KGV"].value == NA
    assert figures["KGV"].chart is None
    assert "Noch nicht angebunden" in figures["KGV"].help


def test_missing_resources_show_not_available_instead_of_numbers(bundle):
    bundle["info"]["financialCurrency"] = None
    del bundle["dividends"]
    figures = {f.label: f for f in key_figures(bundle)}
    for label in ("FCF-Marge", "ROIC", "Dividendenrendite"):
        assert figures[label].value == NA, label
        assert figures[label].delta is None
    assert "Berichtswährung fehlt" in figures["ROIC"].help
    assert figures["Kurs in USD · 1 Jahr"].value != NA


def test_stale_last_value_is_not_reported(bundle):
    symbol = bundle["symbol"]
    # A missing last close must not fall back to an older dividend yield.
    bundle["prices"][symbol].iloc[
        -1, bundle["prices"][symbol].columns.get_loc("Close")
    ] = float("nan")
    figure = {f.label: f for f in key_figures(bundle)}["Dividendenrendite"]
    assert figure.value == NA


def test_headline_for_statements_daily_prices_and_rebased_series(bundle):
    focus = ("MSFT", "MSFT")
    growth = headline(build(10, bundle), focus)
    assert growth.startswith("Umsatz zuletzt ")
    assert "Mrd. USD (30.06.2026)" in growth and "ggü. Vorperiode" in growth
    price = headline(build(1, bundle), focus)
    assert price.startswith("MSFT zuletzt ") and "im gezeigten Zeitraum" in price
    assert headline(build(5, bundle), focus).startswith("MSFT: aus 100 wurden ")
    assert headline(build(21, bundle), focus).startswith("ROIC zuletzt ")
    assert "Vorperiode" in headline(build(21, bundle), focus)


def test_headline_uses_reporting_currency(bundle):
    company = DEFAULT_COMPANIES[3]
    sap = demo_bundle(company)
    text = headline(build(10, sap), (sap["symbol"], company.name))
    assert "Mrd. EUR" in text and "USD" not in text


def test_headline_falls_back_to_note_without_dated_series(bundle):
    reaction = build(11, bundle)
    assert headline(reaction) == reaction.note.split(". ")[0].rstrip(".") + "."
    assert headline(Analysis("leer", note="Berichtswährung: EUR. Nur Tabelle.")) == (
        "Nur Tabelle."
    )
