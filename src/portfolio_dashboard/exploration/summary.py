"""Key figures and one-line takeaways for the analysis page.

No network or Streamlit imports. Fundamental figures reuse ``charts.build`` so
the KPI tiles apply exactly the same currency, listing and data checks as the
corresponding topic charts; a failed check shows "n. v." with its reason.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .charts import AnalysisUnavailable, build

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


def _periodic(bundle, topic, panel, column, label, unit, help_text):
    """Build a fundamental tile from the last value of a topic panel.

    :param bundle: analysis bundle
    :param topic: topic number in ``charts.TITLES``
    :param panel: panel index within the topic
    :param column: data column of the panel
    :param label: tile label
    :param unit: unit suffix of the value
    :param help_text: explanation shown in the tooltip
    :return: KeyFigure, "n. v." with reason on failed checks
    """
    try:
        series = build(topic, bundle).panels[panel].data[column].dropna()
    except DATA_ERRORS as exc:
        reason = str(exc) if isinstance(exc, AnalysisUnavailable) else NA
        return KeyFigure(label, help=f"{help_text} {reason}")
    if series.empty:
        return KeyFigure(label, help=help_text)
    value = float(series.iloc[-1])
    change = None
    if len(series) > 1 and unit == "%":
        change = delta(value - float(series.iloc[-2]), "Pp.")
    chart = [float(x) for x in series.tail(PERIODIC_MAX_POINTS)]
    return KeyFigure(
        label,
        f"{number(value)} {unit}".strip(),
        change,
        chart if len(chart) > 1 else None,
        "bar" if len(chart) <= PERIODIC_MAX_POINTS // 4 else "line",
        f"{help_text} Stand {series.index[-1]:%d.%m.%Y}.",
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
    year = close.loc[end - pd.DateOffset(years=1) :]
    change = (year.iloc[-1] / year.iloc[0] - 1) * 100 if year.iloc[0] > 0 else None
    price = KeyFigure(
        f"Kurs in {currency} · 1 Jahr" if currency else "Kurs · 1 Jahr",
        number(year.iloc[-1], 2),
        None if change is None else delta(change, "%"),
        [float(x) for x in year],
        "area",
        f"Letzter Schlusskurs am {end:%d.%m.%Y} in Handelswährung, "
        "unbereinigt; Veränderung über ein Jahr ohne Dividenden.",
    )
    high = year.max()
    distance = (year / year.cummax() - 1) * 100
    drawdown = KeyFigure(
        "Abstand zum Hoch",
        number(distance.iloc[-1]) + " %",
        None,
        [float(x) for x in distance],
        "area",
        f"Abstand zum 52-Wochen-Hoch von {number(high, 2)} {currency} "
        f"am {year.idxmax():%d.%m.%Y}; Kurve: Abstand zum jeweils bisherigen Hoch.",
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
        ),
        _dividend(bundle),
    ]


def _dividend(bundle):
    """Dividend yield over the prior twelve months, as in topic 14.

    :param bundle: analysis bundle
    :return: KeyFigure
    """
    label = "Dividendenrendite"
    help_text = "Ex-Dividenden der letzten zwölf Monate / Kurs (wie Thema „Dividende und Deckung“)."
    try:
        series = build(14, bundle).panels[1].data["Dividendenrendite"]
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


def _fallback(note):
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


def headline(analysis, focus=()):
    """Summarize the main series of an analysis in one German sentence.

    Uses the first chart panel on a date axis: last value with date and the
    change to the previous period (statements) or over the shown range (daily
    series). Without a dated, unit-labelled series the topic note is used.

    :param analysis: ``charts.Analysis``
    :param focus: symbol and name of the selected company
    :return: one-line takeaway
    """
    fallback = _fallback(analysis.note)
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
    periodic = len(series) <= PERIODIC_MAX_POINTS
    relative = unit.startswith("%") or unit == "×"
    places = 1 if periodic or relative else 2
    if unit == "Start = 100":
        return f"{label}: aus 100 wurden {number(last)} ({stamp:%d.%m.%Y})."
    text = f"{label} zuletzt {number(last, places)} {unit} ({stamp:%d.%m.%Y})"
    if len(series) < 2:
        return text + "."
    first = float(series.iloc[-2] if periodic else series.iloc[0])
    if relative:
        if periodic:
            return text + f", Vorperiode {number(first, places)} {unit}."
        low, high = number(series.min(), places), number(series.max(), places)
        return text + f", Spanne {low} bis {high} {unit}."
    if first <= 0 or last <= 0:
        return text + "."
    change = number((last / first - 1) * 100, sign=True)
    scope = "ggü. Vorperiode" if periodic else "im gezeigten Zeitraum"
    return text + f", {change} % {scope}."
