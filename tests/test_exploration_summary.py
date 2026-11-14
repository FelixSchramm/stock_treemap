"""Key figures and takeaways of the analysis page, with synthetic data only."""

import re

import numpy as np
import pandas as pd
import pytest

from portfolio_dashboard.exploration.charts import TITLES, Analysis, build
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
    assert "Mrd. USD (30.06.2026)" in growth
    assert re.search(r"% ggü\. \d{2}\.\d{2}\.\d{4}\.$", growth)
    price = headline(build(1, bundle), focus)
    assert price.startswith("MSFT zuletzt ") and "im gezeigten Zeitraum" in price
    assert headline(build(5, bundle), focus).startswith("MSFT: aus 100 wurden ")
    assert headline(build(21, bundle), focus).startswith("ROIC zuletzt ")
    assert re.search(r", am \d{2}\.\d{2}\.\d{4} [\d.,−]+ %\.$", headline(build(21, bundle), focus))


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


def _latest(frame):
    return max(frame.columns)


def test_pe_tile_shows_not_available_for_a_loss_instead_of_an_old_ratio(bundle):
    # The last four quarterly EPS sum up negative: topic 12 marks the ratio NaN.
    bundle["earnings"].iloc[-4:, bundle["earnings"].columns.get_loc("Reported EPS")] = -1.0
    assert build(12, bundle).panels[0].data["KGV"].dropna().size > 0
    figure = {f.label: f for f in key_figures(bundle)}["KGV"]
    assert figure.value == NA
    assert figure.chart is None
    assert "negativ oder unvollständig" in figure.help


def test_roic_tile_shows_not_available_for_a_pre_tax_loss_year(bundle):
    bundle["income"].loc["Pretax Income", _latest(bundle["income"])] = -5e9
    figure = {f.label: f for f in key_figures(bundle)}["ROIC"]
    assert figure.value == NA
    assert figure.delta is None
    assert "Vorsteuerverlust" in figure.help


def test_fcf_margin_tile_shows_not_available_without_latest_revenue(bundle):
    bundle["income"].loc["Total Revenue", _latest(bundle["income"])] = np.nan
    figure = {f.label: f for f in key_figures(bundle)}["FCF-Marge"]
    assert figure.value == NA
    assert "Umsatz fehlt" in figure.help


def test_period_delta_needs_the_directly_preceding_period(bundle):
    columns = sorted(bundle["income"].columns)
    bundle["income"].loc["Total Revenue", columns[-2]] = np.nan
    figure = {f.label: f for f in key_figures(bundle)}["FCF-Marge"]
    assert figure.value != NA
    assert figure.delta is None


def test_dividend_yield_does_not_depend_on_payout_ratio_inputs(bundle):
    bundle["cash"] = bundle["cash"].drop(index="Cash Dividends Paid")
    with pytest.raises(Exception):
        build(14, bundle)
    figure = {f.label: f for f in key_figures(bundle)}["Dividendenrendite"]
    assert figure.value.endswith(" %")


def test_company_without_dividends_says_so(bundle):
    bundle["dividends"] = bundle["dividends"].iloc[:0]
    figure = {f.label: f for f in key_figures(bundle)}["Dividendenrendite"]
    assert figure.value == "keine"
    assert "keine Ex-Dividenden" in figure.help


def test_price_tile_names_the_real_start_of_a_short_history(bundle):
    symbol = bundle["symbol"]
    bundle["prices"][symbol] = bundle["prices"][symbol].tail(60)
    start = bundle["prices"][symbol].index[0]
    labels = [f.label for f in key_figures(bundle)]
    assert labels[0] == f"Kurs in USD · seit {start:%d.%m.%Y}"
    assert "52 Wochen" not in key_figures(bundle)[1].help


def test_headline_names_the_compared_date_across_gaps():
    index = pd.to_datetime(["2026-03-31", "2026-06-30"])
    analysis = Analysis("Test")
    analysis.add("Panel", pd.Series([3.0, 2.0], index=index, name="Käufe"), "Mio. USD", "bar")
    text = headline(analysis)
    assert text == "Käufe zuletzt 2,00 Mio. USD (30.06.2026), −33,3 % ggü. 31.03.2026."


def test_headline_keeps_small_amounts_readable_and_detects_monthly_series():
    # 60 monthly dividends: more than 40 points, still periodic, not "im gezeigten Zeitraum".
    index = pd.date_range("2021-07-15", periods=60, freq="MS")
    analysis = Analysis("Test")
    analysis.add("Panel", pd.Series(0.24, index=index, name="Dividende"), "USD / Aktie", "bar")
    text = headline(analysis)
    assert "0,24 USD / Aktie" in text
    assert "ggü. 01.06.2026" in text
    assert "gezeigten Zeitraum" not in text


def test_insider_headline_uses_the_note(bundle):
    analysis = build(17, bundle)
    assert analysis.title == TITLES[17]
    assert headline(analysis) == analysis.note.split(". ")[0].rstrip(".") + "."


def test_months_labels_include_six_months():
    from portfolio_dashboard.exploration import view

    assert [view._months_label(n) for n in view.MONTHS] == ["6 Monate", "1 Jahr", "3 Jahre", "5 Jahre"]
