"""Key figures and one-line takeaways for the analysis page.

No network or Streamlit imports. Fundamental figures reuse ``charts.build`` so
the KPI tiles apply exactly the same currency, listing and data checks as the
corresponding topic charts; a failed check shows "n. v." with its reason.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .calculations import dividend_yield
from .charts import TITLES, AnalysisUnavailable, build
from .data import currency_code, dates

NA = "n. v."
DATA_ERRORS = (
    AnalysisUnavailable,
    KeyError,
    ValueError,
    IndexError,
    TypeError,
    AttributeError,
    ZeroDivisionError,
)
# Statement series have at most a few dozen points; daily prices have hundreds.
PERIODIC_MAX_POINTS = 40
# Main series would show only one side (insider purchases without sales).
NOTE_ONLY = {TITLES[17]}


@dataclass
class KeyFigure:
    """One KPI tile: preformatted texts plus an optional sparkline."""

    label: str
    value: str = NA
    delta: str | None = None
    chart: list[float] | None = None
    chart_type: str = "line"
    help: str = ""


def number(value, places=1, sign=False):
    """Format a float in German notation.

    :param value: number to format
    :param places: decimal places
    :param sign: prefix positive values with "+"
    :return: formatted text, e.g. "1.234,5" or "+2,0"
    """
    text = f"{value:{'+' if sign else ''},.{places}f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".").replace("-", "−")


def delta(value, unit):
    """Format a metric delta; Streamlit colors it only with an ASCII minus.

    :param value: signed change
    :param unit: unit suffix, e.g. "%" or "Pp."
    :return: text such as "+1,2 %" or "-0,4 Pp."
    """
    return f"{number(value, sign=True).replace('−', '-')} {unit}"


def _last(series):
    """Return the last observation if present, never an older one.

    :param series: chronological values
    :return: last value or None when missing
    """
    series = series.replace([np.inf, -np.inf], np.nan)
    if series.empty or pd.isna(series.iloc[-1]):
        return None
    return float(series.iloc[-1])


def _periodic(bundle, topic, panel, column, label, unit, help_text, missing):
    """Build a fundamental tile from the latest period of a topic panel.

    The latest period of the unfiltered series counts: if the topic marks it
    invalid (NaN, e.g. negative TTM EPS or a pre-tax loss), the tile shows
    "n. v." instead of falling back to an older value.

    :param bundle: analysis bundle
    :param topic: topic number in ``charts.TITLES``
    :param panel: panel index within the topic
    :param column: data column of the panel
    :param label: tile label
    :param unit: unit suffix of the value
    :param help_text: explanation shown in the tooltip
    :param missing: reason shown when the latest period has no valid value
    :return: KeyFigure, "n. v." with reason on failed checks
    """
    try:
        raw = build(topic, bundle).panels[panel].data[column]
    except DATA_ERRORS as exc:
        reason = str(exc) if isinstance(exc, AnalysisUnavailable) else NA
        return KeyFigure(label, help=f"{help_text} {reason}")
    raw = raw.replace([np.inf, -np.inf], np.nan)
    if raw.empty:
        return KeyFigure(label, help=help_text)
    stamp = raw.index[-1]
    if pd.isna(raw.iloc[-1]):
        return KeyFigure(label, help=f"{help_text} Stand {stamp:%d.%m.%Y}: {missing}")
    value = float(raw.iloc[-1])
    change = None
    # Only the directly preceding period is a valid comparison.
    if len(raw) > 1 and unit == "%" and not pd.isna(raw.iloc[-2]):
        change = delta(value - float(raw.iloc[-2]), "Pp.")
    chart = [float(x) for x in raw.dropna().tail(PERIODIC_MAX_POINTS)]
    return KeyFigure(
        label,
        f"{number(value)} {unit}".strip(),
        change,
        chart if len(chart) > 1 else None,
        "bar" if len(chart) <= PERIODIC_MAX_POINTS // 4 else "line",
        f"{help_text} Stand {stamp:%d.%m.%Y}.",
    )


def key_figures(bundle):
    """Build the KPI row: price, distance to high, P/E, FCF margin, ROIC, yield.

    :param bundle: analysis bundle of the selected company
    :return: list of six KeyFigure tiles
    """
    symbol = bundle["symbol"]
    currency = bundle["currencies"].get(symbol) or ""
    close = bundle["prices"][symbol].Close.dropna()
    end = close.index[-1]
    year_ago = end - pd.DateOffset(years=1)
    year = close.loc[year_ago:]
    # Shorter histories are labelled with their real start, not as one year.
    full_year = close.index[0] <= year_ago
    span = "1 Jahr" if full_year else f"seit {year.index[0]:%d.%m.%Y}"
    change = (year.iloc[-1] / year.iloc[0] - 1) * 100 if year.iloc[0] > 0 else None
    price = KeyFigure(
        f"Kurs in {currency} · {span}" if currency else f"Kurs · {span}",
        number(year.iloc[-1], 2),
        None if change is None else delta(change, "%"),
        [float(x) for x in year],
        "area",
        f"Letzter Schlusskurs am {end:%d.%m.%Y} in Handelswährung, splitbereinigt, "
        f"nicht dividendenbereinigt; Veränderung {'über ein Jahr' if full_year else span} "
        "ohne Dividenden.",
    )
    high = year.max()
    distance = (year / year.cummax() - 1) * 100
    drawdown = KeyFigure(
        "Abstand zum Hoch",
        number(distance.iloc[-1]) + " %",
        None,
        [float(x) for x in distance],
        "area",
        f"Abstand zum höchsten Schlusskurs {'der letzten 52 Wochen' if full_year else span} "
        f"({number(high, 2)} {currency} am {year.idxmax():%d.%m.%Y}); "
        "Kurve: Abstand zum jeweils bisherigen Hoch.",
    )
    return [
        price,
        drawdown,
        _periodic(
            bundle,
            12,
            0,
            "KGV",
            "KGV",
            "×",
            "Kurs / Summe der vier zuletzt veröffentlichten Quartals-EPS "
            "(wie Thema „Historisches KGV“).",
            "Die Summe der letzten vier Quartals-EPS ist negativ oder unvollständig.",
        ),
        _periodic(
            bundle,
            9,
            1,
            "Free Cashflow",
            "FCF-Marge",
            "%",
            "Free Cashflow / Umsatz des letzten Geschäftsjahres "
            "(wie Thema „Cashflow und Investitionen“).",
            "Umsatz oder Cashflow-Positionen fehlen für diese Periode, oder der Umsatz "
            "ist nicht positiv.",
        ),
        _periodic(
            bundle,
            21,
            0,
            "ROIC",
            "ROIC",
            "%",
            "NOPAT / mittleres investiertes Kapital des letzten Geschäftsjahres "
            "(wie Thema „Kapitalrendite“).",
            "Nicht berechenbar, z. B. Vorsteuerverlust, ungewöhnliche Steuerquote "
            "oder fehlendes investiertes Kapital.",
        ),
        _dividend(bundle),
    ]


def _dividend(bundle):
    """Dividend yield over the prior twelve months, as in topic 14.

    Computed directly from ex-dividends and closes so that missing payout-ratio
    inputs (topic 14, panel 3) do not hide a computable yield.

    :param bundle: analysis bundle
    :return: KeyFigure
    """
    label = "Dividendenrendite"
    help_text = "Ex-Dividenden der letzten zwölf Monate / Kurs (wie Thema „Dividende und Deckung“)."
    try:
        if currency_code(bundle.get("info", {}).get("financialCurrency")) is None:
            raise AnalysisUnavailable(
                "n. v. – Berichtswährung fehlt in den Yahoo-Metadaten."
            )
        payouts = dates(bundle["dividends"])
        close = bundle["prices"][bundle["symbol"]].Close.tail(1260)
        if close.dropna().empty:
            raise AnalysisUnavailable("Kurse fehlen.")
        if payouts.dropna().empty:
            return KeyFigure(
                label,
                "keine",
                help=f"{help_text} Yahoo meldet keine Ex-Dividenden für dieses Unternehmen.",
            )
        series = dividend_yield(payouts, close).replace([np.inf, -np.inf], np.nan)
    except DATA_ERRORS as exc:
        reason = str(exc) if isinstance(exc, AnalysisUnavailable) else NA
        return KeyFigure(label, help=f"{help_text} {reason}")
    value = _last(series)
    if value is None:
        return KeyFigure(label, help=help_text)
    chart = [float(x) for x in series.dropna().tail(260)]
    return KeyFigure(
        label,
        f"{number(value, 2)} %",
        None,
        chart if len(chart) > 1 else None,
        "line",
        f"{help_text} Stand {series.index[-1]:%d.%m.%Y}; Kurve: ein Jahr.",
    )


def note_sentence(note):
    """First sentence of a topic note, skipping the reporting-currency prefix.

    :param note: ``Analysis.note``
    :return: one sentence or empty text
    """
    for sentence in note.split(". "):
        sentence = sentence.strip().rstrip(".")
        if sentence and not sentence.startswith("Berichtswährung"):
            return sentence + "."
    return ""


def _main_column(data, focus):
    """Prefer the selected company's column, then a close price, then the first.

    :param data: panel data
    :param focus: labels identifying the selected company
    :return: column label
    """
    for label in (*focus, "Close"):
        if label in data.columns:
            return label
    return data.columns[0]


def _is_periodic(series):
    """Statement-like series: observations at least about a month apart.

    :param series: dated values without gaps
    :return: True for monthly or sparser data, False for daily prices
    """
    if len(series) < 2:
        return True
    return series.index.to_series().diff().median() >= pd.Timedelta(days=25)


def _amount_places(value):
    """Two decimals for small amounts (e.g. 0,24 USD dividend), else one.

    :param value: amount to format
    :return: decimal places
    """
    return 2 if abs(value) < 10 else 1


def headline(analysis, focus=()):
    """Summarize the main series of an analysis in one German sentence.

    Uses the first chart panel on a date axis: last value with date and the
    change to the previous observation (statements, named with its date) or over
    the shown range (daily series). Without a dated, unit-labelled series, or
    for topics whose main series would be one-sided, the topic note is used.

    :param analysis: ``charts.Analysis``
    :param focus: symbol and name of the selected company
    :return: one-line takeaway
    """
    fallback = note_sentence(analysis.note)
    if analysis.title in NOTE_ONLY:
        return fallback
    panel = next((p for p in analysis.panels if p.figure is not None), None)
    if panel is None or panel.data.empty or not panel.figure.data:
        return fallback
    axis = pd.Index(
        panel.figure.data[0].x if panel.figure.data[0].x is not None else []
    )
    dated = isinstance(panel.data.index, pd.DatetimeIndex)
    if not dated or not pd.api.types.is_datetime64_any_dtype(axis):
        return fallback
    # The y-axis title carries the reporting currency after build()'s USD swap.
    unit = panel.figure.layout.yaxis.title.text or ""
    if not unit:
        return fallback
    column = _main_column(panel.data, focus)
    series = panel.data[column].replace([np.inf, -np.inf], np.nan).dropna()
    if series.empty:
        return fallback
    label = "Schlusskurs" if column == "Close" else column
    last, stamp = float(series.iloc[-1]), series.index[-1]
    periodic = _is_periodic(series)
    relative = unit.startswith("%") or unit == "×"
    places = 1 if relative else (_amount_places(last) if periodic else 2)
    if unit == "Start = 100":
        return f"{label}: aus 100 wurden {number(last)} ({stamp:%d.%m.%Y})."
    text = f"{label} zuletzt {number(last, places)} {unit} ({stamp:%d.%m.%Y})"
    if len(series) < 2:
        return text + "."
    # Periodic comparisons name the compared date: gaps stay visible.
    first = float(series.iloc[-2] if periodic else series.iloc[0])
    before = series.index[-2]
    if relative:
        if periodic:
            return text + f", am {before:%d.%m.%Y} {number(first, places)} {unit}."
        low, high = number(series.min(), places), number(series.max(), places)
        return text + f", Spanne {low} bis {high} {unit}."
    if first <= 0 or last <= 0:
        return text + "."
    change = number((last / first - 1) * 100, sign=True)
    scope = f"ggü. {before:%d.%m.%Y}" if periodic else "im gezeigten Zeitraum"
    return text + f", {change} % {scope}."
