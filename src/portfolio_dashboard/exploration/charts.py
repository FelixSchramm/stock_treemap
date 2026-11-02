"""Native Plotly port of Financial Pipeline V4's 29 analysis themes.

No network or Streamlit imports. Missing resources invalidate only their topic.
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from portfolio_dashboard.design import (
    COLORS,
    DARK,
    DOWN,
    UP,
    VALUE_COLORS,
    style_figure,
)

from .calculations import (
    dividend_yield,
    drawdown,
    equity_value,
    implied_growth,
    monthly_returns,
    roic,
    trailing_four,
    valid_calls,
)
from .data import NAMES, TICKERS, dates, prices, statement

GROUPS = {
    "Geschäftsmodell": (28, 10, 9, 29, 3, 22),
    "Kapital & Bilanz": (21, 23, 24, 20, 14),
    "Bewertung": (25, 12, 26, 27),
    "Kurs & Risiko": (1, 2, 4, 5, 6, 7, 8, 13, 11),
    "Marktumfeld": (15, 16, 17, 18, 19),
}
TITLES = {
    1: "Kurs in Euro",
    2: "Kurs und Quartalsergebnisse",
    3: "Cashflow je Quartal",
    4: "Kerzen und Volumen",
    5: "Was wurde aus 100?",
    6: "Monatsrenditen",
    7: "Verlust seit dem Hoch",
    8: "Rendite und Kursrisiko",
    9: "Cashflow und Investitionen",
    10: "Wachstum und Margen",
    11: "Reaktion auf Quartalszahlen",
    12: "Historisches KGV",
    13: "Vergleich mit dem Markt",
    14: "Dividende und Deckung",
    15: "Analysten-Konsens",
    16: "Kurszieländerungen",
    17: "Insider-Transaktionen",
    18: "Institutionelle Eigentümer",
    19: "Optionsvolatilität",
    20: "Bilanz und Zinsdeckung",
    21: "Kapitalrendite",
    22: "Gewinnqualität",
    23: "Kapitalverwendung",
    24: "Entwicklung je Aktie",
    25: "Schätzungen und Revisionen",
    26: "Bewertungsmultiplikatoren",
    27: "Reverse DCF",
    28: "Geschäftsmodell und Segmente",
    29: "Cashflow-Überleitung",
}
CF = ["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow"]
CF_NAMES = dict(zip(CF, ["Operativer CF", "Cash-Capex", "Free Cashflow"]))


class AnalysisUnavailable(ValueError):
    """A safe, user-facing explanation of an invalid financial analysis."""


@dataclass
class Panel:
    title: str
    data: pd.DataFrame
    figure: go.Figure | None = None
    unit: str = ""


@dataclass
class Analysis:
    title: str
    note: str = ""
    panels: list[Panel] = field(default_factory=list)
    source: str = "Yahoo Finance / yfinance"

    def add(self, title, data, unit="", kind="line", *, colors=None):
        data = data.to_frame() if isinstance(data, pd.Series) else data.copy()
        data = data.replace([np.inf, -np.inf], np.nan)
        if data.empty or data.isna().all().all():
            raise AnalysisUnavailable(f"{title}: keine ausreichenden Daten.")
        fig = go.Figure()
        for i, col in enumerate(data.columns):
            color = (colors or {}).get(col, COLORS[i % len(COLORS)])
            args = dict(
                x=data.index,
                y=data[col],
                name=str(col),
                hovertemplate=f"%{{x}}<br>{col}: %{{y:,.2f}} {unit}<extra></extra>",
            )
            if kind in ("bar", "stack"):
                fig.add_trace(go.Bar(**args, marker_color=color))
            else:
                fig.add_trace(
                    go.Scatter(
                        **args,
                        mode="markers" if kind == "scatter" else "lines",
                        line=dict(color=color, width=2),
                        marker=dict(color=color, size=8),
                        connectgaps=False,
                    )
                )
        fig.update_layout(
            height=340,
            hovermode="closest" if kind == "scatter" else "x unified",
            barmode="stack" if kind == "stack" else "group",
            yaxis_title=unit,
        )
        style_figure(fig)
        self.panels.append(Panel(title, data, fig, unit))
        return fig

    def table(self, title, data):
        if data.empty:
            raise AnalysisUnavailable(f"{title}: keine Daten.")
        self.panels.append(Panel(title, data))


def cash(bundle, quarterly=False):
    frame = statement(bundle, "quarter_cash" if quarterly else "cash", CF).dropna()
    if frame.empty:
        raise AnalysisUnavailable("Cashflow-Positionen fehlen.")
    frame["Capital Expenditure"] = frame["Capital Expenditure"].abs()
    if not np.allclose(frame[CF[0]] - frame[CF[1]], frame[CF[2]], rtol=0.002, atol=1e6):
        raise AnalysisUnavailable(
            "Anbieter-FCF passt nicht zu operativem CF minus Cash-Capex."
        )
    return frame


def pe_series(bundle):
    eps = dates(bundle["earnings"].dropna(subset=["Reported EPS"]))["Reported EPS"]
    eps = eps[~eps.index.duplicated()]
    consecutive = (
        eps.index.to_series().diff().dt.days.between(60, 130).rolling(3).sum().eq(3)
    )
    ttm = eps.rolling(4).sum().where(consecutive)
    ttm.index += pd.Timedelta(days=1)
    close = bundle["prices"]["MSFT"].Close
    # Reindex with method keeps explicit missing EPS windows missing.
    known = ttm.reindex(close.index, method="ffill")
    return (close / known.where(known > 0)).rename("KGV")


def comparison_window(bundle, currency, total, months):
    history = prices(bundle, currency=currency, total=total)
    common = history.dropna()
    if len(common) < 2:
        raise AnalysisUnavailable("Keine gemeinsamen Vergleichstage.")
    end = common.index[-1]
    common = common.loc[end - pd.DateOffset(months=months) : end]
    if len(common) < 2:
        raise AnalysisUnavailable("Zu wenige Kurse im Vergleichszeitraum.")
    return history.loc[:end], common.index[0], end


def build(number, bundle, currency="EUR", total=True, months=12):
    a = Analysis(TITLES[number])
    if number in {3, 9, 10, 14, 20, 21, 22, 23, 24, 27, 29}:
        if bundle.get("info", {}).get("financialCurrency") != "USD":
            raise AnalysisUnavailable("Berichtswährung USD ist nicht bestätigt.")
    if number in {3, 9, 29}:
        cf = cash(bundle, number == 3)
        a.note = "CFO − absoluter Cash-Capex = FCF. Geschäftsjahresende Juni; Cash-Capex trennt Erhaltungs- und Wachstumsinvestitionen nicht."
        if number == 29:
            row = cf.iloc[-1] / 1e9
            values = [row[CF[0]], -row[CF[1]], row[CF[2]]]
            fig = go.Figure(
                go.Waterfall(
                    x=["Operativer CF", "Cash-Capex", "Free Cashflow"],
                    y=values,
                    measure=["absolute", "relative", "total"],
                    increasing=dict(marker_color=UP),
                    decreasing=dict(marker_color=DOWN),
                    totals=dict(marker_color=COLORS[2]),
                    text=[f"{x:,.2f}" for x in values],
                    hovertemplate="%{x}: %{y:,.2f} Mrd. USD<extra></extra>",
                )
            )
            fig.update_layout(height=390, yaxis_title="Mrd. USD")
            a.panels.append(
                Panel(
                    f"Geschäftsjahr bis {cf.index[-1]:%d.%m.%Y}",
                    cf.tail(1).rename(columns=CF_NAMES) / 1e9,
                    style_figure(fig),
                )
            )
        else:
            a.add(
                "Einzelperioden", cf.rename(columns=CF_NAMES) / 1e9, "Mrd. USD", "bar"
            )
            if number == 3:
                a.add(
                    "Vier aufeinanderfolgende Quartale",
                    trailing_four(cf).rename(columns=CF_NAMES) / 1e9,
                    "Mrd. USD",
                )
                a.note += " TTM überbrückt keine fehlenden Quartale."
            else:
                revenue = statement(bundle, "income", ["Total Revenue"])[
                    "Total Revenue"
                ]
                ratios = (
                    cf[[CF[1], CF[2]]].div(revenue.where(revenue > 0), axis=0) * 100
                )
                a.add(
                    "Investitionsintensität und FCF-Marge",
                    ratios.rename(columns=CF_NAMES),
                    "%",
                )
    elif number == 10:
        for resource, label in [("income", "Jahr"), ("quarter_income", "Quartal")]:
            inc = statement(
                bundle,
                resource,
                ["Total Revenue", "Net Income", "Operating Income", "Gross Profit"],
            )
            a.add(
                f"{label} · Umsatz und Gewinn",
                inc[["Total Revenue", "Net Income"]].rename(
                    columns={"Total Revenue": "Umsatz", "Net Income": "Gewinn"}
                )
                / 1e9,
                "Mrd. USD",
                "bar",
            )
            a.add(
                f"{label} · Margen",
                inc[["Gross Profit", "Operating Income", "Net Income"]].div(
                    inc["Total Revenue"].where(inc["Total Revenue"] > 0), axis=0
                )
                * 100,
                "%",
            )
            prior = inc["Total Revenue"].reindex(inc.index - pd.DateOffset(years=1))
            prior.index = inc.index
            a.add(
                f"{label} · Umsatzwachstum zum Vorjahr",
                ((inc["Total Revenue"] / prior.where(prior > 0) - 1) * 100).rename(
                    "Umsatzwachstum"
                ),
                "%",
            )
        a.note = "Jahre und Quartale getrennt. Wachstum nur bei vorhandenem Vorjahreszeitpunkt; Microsoft-Geschäftsjahr endet im Juni."
    elif number == 20:
        debt, liquid, leases = (
            "Total Debt",
            "Cash Cash Equivalents And Short Term Investments",
            "Capital Lease Obligations",
        )
        bs = statement(bundle, "balance", [debt, liquid, leases])
        a.add(
            "Schulden, Liquidität und Leasing",
            bs.rename(
                columns={
                    debt: "Gesamtschulden",
                    liquid: "Cash + kurzfristige Anlagen",
                    leases: "Leasing (enthalten, nicht addieren)",
                }
            )
            / 1e9,
            "Mrd. USD",
            "bar",
        )
        a.add(
            "Nettofinanzschulden-Proxy",
            ((bs[debt] - bs[liquid]) / 1e9).rename("Schulden minus Liquidität"),
            "Mrd. USD",
        )
        inc = statement(bundle, "income", ["Operating Income", "Interest Expense"])
        a.add(
            "Zinsdeckung",
            (
                inc["Operating Income"]
                / inc["Interest Expense"].where(inc["Interest Expense"] > 0)
            ).rename("EBIT / Zinsen"),
            "×",
        )
        a.note = "Leasing nicht doppelt zu Gesamtschulden addieren. Fälligkeiten und Covenants fehlen."
    elif number == 21:
        inc = statement(
            bundle,
            "income",
            [
                "Operating Income",
                "Tax Provision",
                "Pretax Income",
                "Total Revenue",
                "Research And Development",
            ],
        )
        capital = statement(bundle, "balance", ["Invested Capital"])["Invested Capital"]
        a.add(
            "Rendite auf durchschnittliches Kapital",
            roic(inc, capital).rename("ROIC"),
            "%",
        )
        capex = statement(bundle, "cash", ["Capital Expenditure"])[
            "Capital Expenditure"
        ].abs()
        a.add(
            "Investitionsintensität",
            pd.DataFrame(
                {"Cash-Capex": capex, "F&E": inc["Research And Development"]}
            ).div(inc["Total Revenue"].where(inc["Total Revenue"] > 0), axis=0)
            * 100,
            "% vom Umsatz",
        )
        a.note = "NOPAT / mittleres Jahresanfangs- und Endkapital, inkl. Goodwill. Berichtssteuerquote nur bei positivem Vorsteuergewinn und 0–100 %. Keine F&E-Aktivierung."
    elif number == 22:
        cf = statement(
            bundle,
            "cash",
            [CF[0], CF[2], "Stock Based Compensation", "Change In Working Capital"],
        )
        inc = statement(bundle, "income", ["Net Income", "Total Revenue"])
        a.add(
            "Gewinn und Zahlungsströme",
            pd.DataFrame(
                {
                    "Nettogewinn": inc["Net Income"],
                    "Operativer CF": cf[CF[0]],
                    "FCF": cf[CF[2]],
                }
            )
            / 1e9,
            "Mrd. USD",
            "bar",
        )
        a.add(
            "Einflüsse auf den operativen CF",
            cf[["Stock Based Compensation", "Change In Working Capital"]].div(
                inc["Total Revenue"].where(inc["Total Revenue"] > 0), axis=0
            )
            * 100,
            "% vom Umsatz",
        )
        a.note = "Aktienvergütung erhöht den operativen Cashflow, hat aber wirtschaftliche Kosten. Nicht doppelt vom bereits belasteten Gewinn abziehen."
    elif number == 23:
        uses = [
            "Repurchase Of Capital Stock",
            "Cash Dividends Paid",
            "Purchase Of Business",
        ]
        funding = ["Issuance Of Debt", "Repayment Of Debt"]
        cf = statement(bundle, "cash", uses + funding + [CF[2]])
        fig = a.add(
            "Kapitalverwendung nach Capex",
            cf[uses]
            .abs()
            .rename(
                columns=dict(zip(uses, ["Rückkäufe", "Dividenden", "Akquisitionen"]))
            )
            / 1e9,
            "Mrd. USD",
            "stack",
        )
        fig.add_trace(
            go.Scatter(
                x=cf.index,
                y=cf[CF[2]] / 1e9,
                name="FCF nach Capex",
                line_color=DARK["ink"],
                hovertemplate="FCF: %{y:,.2f} Mrd. USD<extra></extra>",
            )
        )
        a.panels[-1].data["FCF nach Capex"] = cf[CF[2]] / 1e9
        a.add(
            "Finanzierung separat",
            cf[funding]
            .abs()
            .rename(columns=dict(zip(funding, ["Schuldenaufnahme", "Tilgung"])))
            / 1e9,
            "Mrd. USD",
            "bar",
        )
        a.note = "Bruttozahlungen; keine vollständige Cashflow-Überleitung. Rückkäufe zusammen mit Verwässerung beurteilen."
    elif number == 24:
        inc = statement(
            bundle, "income", ["Diluted Average Shares", "Net Income", "Diluted EPS"]
        )
        a.add(
            "Durchschnittliche verwässerte Aktienzahl",
            (inc["Diluted Average Shares"] / 1e6).rename("Aktien"),
            "Mio.",
        )
        comparison = inc[["Net Income", "Diluted EPS"]].dropna()
        a.add(
            "Gewinn und EPS",
            comparison / comparison.iloc[0].replace(0, np.nan) * 100,
            "Start = 100",
        )
        fcf = statement(bundle, "cash", [CF[2]])[CF[2]]
        a.add(
            "Je Aktie",
            pd.DataFrame(
                {
                    "Verwässertes EPS": inc["Diluted EPS"],
                    "FCF je Aktie": fcf
                    / inc["Diluted Average Shares"].where(
                        inc["Diluted Average Shares"] > 0
                    ),
                }
            ),
            "USD",
        )
        a.note = "Periodendurchschnittliche verwässerte Aktien, keine aktuelle Stichtagszahl als historischer Nenner. FCF je Aktie ist ein Proxy."
    elif number == 28:
        data = bundle.get("segments")
        if data is None:
            data = pd.read_csv(Path(__file__).with_name("segments.csv"))
        for field_name, label in [
            ("revenue_million_usd", "Umsatz"),
            ("operating_income_million_usd", "Operatives Ergebnis"),
        ]:
            frame = (
                data.pivot(index="year", columns="segment", values=field_name) / 1000
            )
            order = (
                ["Demo · Produktivität", "Demo · Cloud", "Demo · Geräte"]
                if bundle.get("synthetic")
                else [
                    "Productivity & Business Processes",
                    "Intelligent Cloud",
                    "More Personal Computing",
                ]
            )
            frame = frame.reindex(columns=order)
            frame.index = frame.index.map(lambda year: f"GJ {year}")
            a.add(label, frame, "Mrd. USD", "stack")
        a.source = (
            "Synthetische Segmentdaten"
            if bundle.get("synthetic")
            else "Microsoft Annual Report 2026 · Note 18 · feste Berichtsversion"
        )
        a.note = "Azure ist Teil von Intelligent Cloud, kein separat ausgewiesener Umsatz. Microsoft Cloud überschneidet Segmente und wird nicht addiert. Segmentdaten werden nicht mit Yahoo aktualisiert."
    elif number in {5, 6, 7, 8, 13}:
        history, start, end = comparison_window(bundle, currency, total, months)
        recent = history.loc[start:end]
        common = recent.dropna()
        colors = {NAMES[t]: COLORS[i] for i, t in enumerate(TICKERS)}
        kind = (
            "Gesamtrendite-Näherung aus bereinigten Kursen"
            if total
            else "Kursrendite ohne Dividenden"
        )
        a.note = f"{currency} · {kind} · {start:%d.%m.%Y}–{end:%d.%m.%Y}. FX nur aus vorherigen Beobachtungen bis 7 Tage; Aktienkurse werden nicht aufgefüllt."
        if number == 5:
            a.add(
                "Gemeinsame Handelstage",
                (common / common.iloc[0] * 100).rename(columns=NAMES),
                "Start = 100",
                colors=colors,
            )
        elif number == 6:
            returns = (
                monthly_returns(history, bundle["retrieved_at"]).rename(columns=NAMES).T
            )
            returns.columns = returns.columns.strftime("%m/%Y")
            fig = go.Figure(
                go.Heatmap(
                    z=returns.to_numpy(),
                    x=returns.columns,
                    y=returns.index,
                    colorscale=VALUE_COLORS,
                    zmin=-10,
                    zmax=10,
                    zmid=0,
                    text=returns.map(
                        lambda x: "n. v." if pd.isna(x) else f"{x:+.1f}%"
                    ).to_numpy(),
                    texttemplate="%{text}",
                    textfont_color="white",
                    hoverongaps=False,
                    hovertemplate="%{y} · %{x}<br>%{z:+.2f}%<extra></extra>",
                    colorbar=dict(title="Rendite %", ticksuffix="%"),
                )
            )
            fig.update_layout(height=390)
            a.panels.append(
                Panel("Letzte zwölf vollständige Monate", returns, style_figure(fig))
            )
            a.note += " Unabhängig vom Zeitraumfilter: 12 vollständige Monate; Farbe bei ±10 % gesättigt, exakter Wert im Hover."
        elif number == 7:
            a.add(
                "Hoch aus der gesamten geladenen Historie",
                drawdown(history).loc[start:end].rename(columns=NAMES),
                "%",
                colors=colors,
            )
            a.note += " Referenzhoch aus bis zu 10 Jahren vor dem Ausschnitt; kein garantiertes Allzeithoch."
        elif number == 8:
            rows = {}
            for ticker in TICKERS:
                series = recent[ticker].dropna()
                full = history[ticker].dropna()
                last_peak = full[full.eq(full.max())].index[-1]
                rows[NAMES[ticker]] = {
                    "Zeitraum (%)": (series.iloc[-1] / series.iloc[0] - 1) * 100,
                    "Letzter Handelstag (%)": series.pct_change(fill_method=None).iloc[
                        -1
                    ]
                    * 100,
                    "Volatilität p. a. (%)": np.log(series / series.shift()).std()
                    * np.sqrt(252)
                    * 100,
                    "Max. Zeitraum-Drawdown (%)": drawdown(series).min(),
                    "Kalendertage seit 10J-Hoch": (end - last_peak).days,
                }
            a.table(
                "Vergleichbare Zeiträume, native Börsenkalender", pd.DataFrame(rows).T
            )
            a.note += " Volatilität: tägliche Log-Renditen × √252. Zeitraum-Drawdown startet am linken Rand, Hoch-Abstand nutzt Vorgeschichte."
        else:
            symbols = ("MSFT", "SPY", "QQQ") if total else ("MSFT", "^GSPC", "^NDX")
            bench = (
                prices(bundle, symbols, currency, total)
                .loc[start:end]
                .dropna()
                .reindex([start, end])
                .dropna()
            )
            if len(bench) != 2:
                raise AnalysisUnavailable(
                    "Benchmarks fehlen an gemeinsamen Start-/Endtagen."
                )
            bench = prices(bundle, symbols, currency, total).loc[start:end].dropna()
            idx = bench / bench.iloc[0] * 100
            a.add(
                "Microsoft und Benchmarks",
                idx,
                "Start = 100",
                colors={
                    "MSFT": COLORS[1],
                    symbols[1]: COLORS[0],
                    symbols[2]: COLORS[2],
                },
            )
            a.add(
                "Microsoft abzüglich Benchmark",
                pd.DataFrame({s: idx.MSFT - idx[s] for s in symbols[1:]}),
                "Prozentpunkte",
            )
            a.note += " SPY/QQQ sind bei Gesamtrendite investierbare Index-Näherungen mit Fondskosten und Tracking-Abweichung."
    elif number in {1, 2}:
        close = prices(bundle, ("MSFT",), "EUR", False).MSFT.dropna()
        close = close.loc[close.index[-1] - pd.DateOffset(months=months) :]
        fig = a.add(
            "Microsoft · unbereinigter Schlusskurs",
            close.rename("MSFT"),
            "EUR",
            colors={"MSFT": COLORS[1]},
        )
        a.note = "USD-Schlusskurs mit Tages-FX in EUR, ohne Dividenden."
        if number == 2:
            eps = bundle["earnings"].dropna(subset=["Reported EPS", "EPS Estimate"])
            events = []
            for stamp, row in eps.iterrows():
                date = pd.Timestamp(stamp).tz_localize(None).normalize()
                if (
                    close.index[0] <= date <= close.index[-1]
                    and row["EPS Estimate"] > 0
                ):
                    surprise = (row["Reported EPS"] / row["EPS Estimate"] - 1) * 100
                    events.append((date, close.asof(date), surprise))
            for date, value, surprise in events:
                fig.add_trace(
                    go.Scatter(
                        x=[date],
                        y=[value],
                        mode="markers",
                        showlegend=False,
                        marker=dict(
                            symbol="triangle-up" if surprise >= 0 else "triangle-down",
                            color=UP if surprise >= 0 else DOWN,
                            size=12,
                        ),
                        text=[f"EPS vs. Schätzung: {surprise:+.2f}%"],
                        hovertemplate="%{x}<br>%{text}<extra></extra>",
                    )
                )
            if events:
                a.table(
                    "Ereignisse",
                    pd.DataFrame(
                        events, columns=["Datum", "Kurs EUR", "EPS-Abweichung (%)"]
                    ),
                )
            a.note += " EPS-Abweichung ist kein Gewinnwachstum; Marker am Berichtstag, nicht am Reaktionstag."
    elif number == 4:
        from plotly.subplots import make_subplots

        full = bundle["prices"]["MSFT"]
        frame = full.copy()
        for n in (20, 50):
            frame[f"SMA {n}"] = full.Close.rolling(n).mean()
        frame = frame.tail(60)
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.75, 0.25],
            vertical_spacing=0.08,
        )
        fig.add_trace(
            go.Candlestick(
                x=frame.index,
                open=frame.Open,
                high=frame.High,
                low=frame.Low,
                close=frame.Close,
                name="OHLC USD",
                increasing=dict(line_color=UP, fillcolor=DARK["surface"]),
                decreasing=dict(line_color=DOWN, fillcolor=DOWN),
            ),
            row=1,
            col=1,
        )
        for i, n in enumerate((20, 50)):
            fig.add_trace(
                go.Scatter(
                    x=frame.index,
                    y=frame[f"SMA {n}"],
                    name=f"SMA {n}",
                    line_color=COLORS[i + 1],
                ),
                row=1,
                col=1,
            )
        fig.add_trace(
            go.Bar(
                x=frame.index,
                y=frame.Volume / 1e6,
                name="Volumen (Mio.)",
                marker_color=DARK["axis"],
            ),
            row=2,
            col=1,
        )
        fig.update_layout(height=550, xaxis_rangeslider_visible=False)
        fig.update_yaxes(title_text="USD", row=1, col=1)
        fig.update_yaxes(title_text="Mio. Aktien", row=2, col=1)
        a.panels.append(Panel("Letzte 60 Handelstage", frame, style_figure(fig)))
        a.note = "Hohl: Schluss ≥ Eröffnung. Gefüllt: Schluss < Eröffnung. Nicht die Vortagsveränderung. SMA aus Vorgeschichte berechnet."
    elif number == 11:
        close = prices(bundle, ("MSFT", "SPY"), "USD", True).dropna()
        rows = []
        for stamp, row in (
            bundle["earnings"]
            .dropna(subset=["Reported EPS", "EPS Estimate"])
            .iterrows()
        ):
            local = pd.Timestamp(stamp)
            if local.tzinfo is not None:
                local = local.tz_convert("America/New_York")
            date = local.tz_localize(None).normalize()
            p = close.index.searchsorted(date)
            before = (local.hour, local.minute) < (9, 30)
            base, after = (p - 1, p) if before else (p, p + 1)
            if (
                base < 0
                or after >= len(close)
                or close.index[p] != date
                or row["EPS Estimate"] <= 0
            ):
                continue
            ret = (close.iloc[after] / close.iloc[base] - 1) * 100
            rows.append(
                {
                    "Datum": date,
                    "EPS-Abweichung (%)": (
                        row["Reported EPS"] / row["EPS Estimate"] - 1
                    )
                    * 100,
                    "MSFT (%)": ret.MSFT,
                    "MSFT − SPY (pp)": ret.MSFT - ret.SPY,
                }
            )
        data = pd.DataFrame(rows).set_index("Datum")
        for col in ["MSFT (%)", "MSFT − SPY (pp)"]:
            fig = go.Figure(
                go.Scatter(
                    x=data["EPS-Abweichung (%)"],
                    y=data[col],
                    mode="markers",
                    text=data.index.strftime("%d.%m.%Y"),
                    marker=dict(color=COLORS[1], size=9),
                    hovertemplate="%{text}<br>EPS: %{x:+.2f}%<br>Reaktion: %{y:+.2f}<extra></extra>",
                )
            )
            fig.update_layout(
                height=350, xaxis_title="EPS über/unter Schätzung (%)", yaxis_title=col
            )
            a.panels.append(Panel(col, data, style_figure(fig)))
        a.note = "Erste Sitzung nach Veröffentlichung; vor 09:30 New York zählt die Berichtssitzung. Bereinigte USD-Renditen; einfache SPY-Renditedifferenz, keine Prognose."
    elif number == 12:
        ratio = pe_series(bundle)
        fig = a.add("Vier zuletzt veröffentlichte Quartals-EPS", ratio, "×")
        fig.add_hline(
            y=ratio.median(), line_dash="dot", annotation_text="Historischer Median"
        )
        a.note = "Gewinne erst ab Folgetag; vier aufeinanderfolgende positive TTM-EPS. Yahoo-Earnings-EPS können von GAAP abweichen. Median ist kein fairer Wert."
    elif number == 14:
        payouts = dates(bundle["dividends"])
        close = bundle["prices"]["MSFT"].Close.tail(1260)
        a.add(
            "Ausschüttung am Ex-Tag",
            payouts.loc[close.index[0] :].rename("Dividende"),
            "USD / Aktie",
            "bar",
        )
        a.add(
            "Tatsächliche Zwölfmonatsrendite",
            dividend_yield(payouts, close).rename("Dividendenrendite"),
            "%",
        )
        cf = statement(bundle, "cash", ["Cash Dividends Paid", CF[2]])
        inc = statement(bundle, "income", ["Net Income"])
        a.add(
            "Jährliche Ausschüttungsquote",
            pd.DataFrame(
                {
                    "vom Gewinn": cf["Cash Dividends Paid"].abs()
                    / inc["Net Income"].where(inc["Net Income"] > 0)
                    * 100,
                    "vom FCF": cf["Cash Dividends Paid"].abs()
                    / cf[CF[2]].where(cf[CF[2]] > 0)
                    * 100,
                }
            ),
            "%",
        )
        a.note = "Ex-Tage im vorangegangenen Kalenderjahr, nicht letzte Rate × 4. Jährliche Deckung nur bei positivem Gewinn bzw. FCF."
    elif number == 15:
        t = bundle["targets"]
        a.add(
            "Aktuelle Kursziele und Kurs",
            pd.Series(
                {k: t[k] for k in ("low", "mean", "median", "high", "current")},
                name="USD",
            ),
            "USD",
            "scatter",
        )
        cols = ["strongBuy", "buy", "hold", "sell", "strongSell"]
        rec = bundle["recommendations"].set_index("period")[cols]
        a.add(
            "Empfehlungsanteile",
            rec.div(rec.sum(axis=1).replace(0, np.nan), axis=0) * 100,
            "%",
            "stack",
        )
        a.table("Anzahl der Empfehlungen", rec)
        a.note = "Kursziele sind Meinungen; Minimum/Maximum sind keine Konfidenzintervalle. Rating- und Kurszielanzahl können abweichen."
    elif number == 16:
        data = dates(bundle["actions"])
        data = data.loc[
            (data.currentPriceTarget > 0) & (data.priorPriceTarget > 0)
        ].tail(12)
        labels = data.index.strftime("%d.%m.%y") + " · " + data.Firm.astype(str)
        frame = data[["priorPriceTarget", "currentPriceTarget"]].copy()
        frame.index = labels
        a.add(
            "Letzte zwölf gemeldete Änderungen",
            frame.rename(
                columns={"priorPriceTarget": "Vorher", "currentPriceTarget": "Nachher"}
            ),
            "USD",
            "bar",
        )
        a.table("Einzelmeldungen", data)
        a.note = (
            "Einzelne Analystenaktionen, kein rekonstruierter historischer Konsens."
        )
    elif number == 17:
        data = bundle["insiders"].copy()
        data["Datum"] = (
            pd.to_datetime(data["Start Date"]).dt.tz_localize(None).dt.normalize()
        )
        end = bundle["prices"]["MSFT"].index[-1]
        data = data.loc[data.Datum.between(end - pd.DateOffset(years=1), end)]
        text = data.Text.fillna("")
        data["Art"] = np.where(
            text.str.startswith("Sale"),
            "Verkäufe",
            np.where(text.str.startswith("Purchase"), "Käufe", "Sonstige"),
        )
        valid = data.loc[(data.Value > 0) & data.Art.isin(["Käufe", "Verkäufe"])]
        if not valid.empty:
            table = (
                valid.pivot_table(
                    index="Datum", columns="Art", values="Value", aggfunc="sum"
                )
                .resample("ME")
                .sum(min_count=1)
            )
            a.add(
                "Gemeldete positive Transaktionswerte", table / 1e6, "Mio. USD", "bar"
            )
        a.table("Alle Meldungen inklusive unbekannter Werte", data)
        a.note = "Fehlende Werte bleiben unbekannt. Verkäufe allein sind kein Negativsignal; Verkaufspläne und Restbeteiligung fehlen."
    elif number == 18:
        data = (
            bundle["holders"]
            .dropna(subset=["Holder", "pctHeld"])
            .nlargest(10, "pctHeld")
            .copy()
        )
        a.add(
            "Gemeldeter Anteil",
            (data.set_index("Holder").pctHeld * 100).rename("Anteil"),
            "%",
            "bar",
        )
        data["Anteil (%)"] = data.pctHeld * 100
        data["Bestandsänderung (%)"] = data.pctChange * 100
        a.table(
            "Meldestichtage und Änderungen",
            data[["Holder", "Anteil (%)", "Bestandsänderung (%)", "Date Reported"]],
        )
        a.note = "Bestandsänderung ist relativ zum Vorbericht, keine Änderung in Prozentpunkten am Unternehmen. Meldedatum ≠ Abrufdatum."
    elif number == 19:
        spot = bundle["prices"]["MSFT"].Close.iloc[-1]
        now = pd.Timestamp(bundle["retrieved_at"])
        fig = go.Figure()
        tables = []
        for i, (expiry, raw) in enumerate(bundle["options"].items()):
            valid = valid_calls(raw, spot, now).copy()
            if valid.empty:
                continue
            valid["Verfall"] = expiry
            tables.append(valid)
            fig.add_trace(
                go.Scatter(
                    x=valid.strike / spot,
                    y=valid.impliedVolatility * 100,
                    mode="markers",
                    name=expiry,
                    marker=dict(color=COLORS[i], size=8),
                    hovertemplate="Strike/Kurs: %{x:.2f}<br>IV: %{y:.2f}%<extra>%{fullData.name}</extra>",
                )
            )
        if not tables:
            raise AnalysisUnavailable(
                "Keine Optionsquotes erfüllen die Qualitätsfilter."
            )
        fig.update_layout(
            height=400, xaxis_title="Strike / letzter Kurs", yaxis_title="IV p. a. (%)"
        )
        a.panels.append(Panel("Gefilterte Calls", pd.concat(tables), style_figure(fig)))
        a.note = "±20 % vom Spot; Bid > 0; Ask ≥ Bid; Spread ≤20 % der Mitte; OI ≥10; letzter Trade ≤3 Tage; IV 1–300 %. Einzelpunkte, keine geglättete Kurve. Trade-Alter garantiert keine aktuelle Quote."
    elif number == 25:
        for key, label, scale, unit in [
            ("estimates", "EPS", 1, "USD"),
            ("revenue_estimates", "Umsatz", 1e9, "Mrd. USD"),
        ]:
            data = bundle[key].reindex(["0y", "+1y"])
            a.add(
                f"{label} · laufendes / nächstes Geschäftsjahr",
                data[["low", "avg", "high"]] / scale,
                unit,
                "bar",
            )
            a.table(f"{label} · Analystenzahl", data[["numberOfAnalysts"]])
        trend = (
            bundle["eps_trend"]
            .reindex(["0y", "+1y"])[
                ["90daysAgo", "60daysAgo", "30daysAgo", "7daysAgo", "current"]
            ]
            .T
        )
        a.add("EPS-Rückblicke für identische Zielperioden", trend, "USD")
        a.add(
            "Revisionen letzte 30 Tage",
            bundle["eps_revisions"].reindex(["0y", "+1y"])[
                ["upLast30days", "downLast30days"]
            ],
            "Anzahl",
            "bar",
        )
        a.note = "0y = laufendes Geschäftsjahr, +1y = nächstes, jeweils Ende Juni. Spannen sind keine Konfidenzintervalle; Rückblicke aus aktuellem Snapshot, keine selbst archivierte Historie."
    elif number == 26:
        for name in ["Trailing P/E", "Forward P/E", "Enterprise Value/EBITDA"]:
            row = bundle["valuation"].loc[name]
            historic = row.drop(labels="Current")
            historic.index = pd.to_datetime(historic.index)
            series = pd.concat(
                [
                    historic,
                    pd.Series(
                        [row["Current"]],
                        index=[
                            pd.Timestamp(bundle["retrieved_at"])
                            .tz_localize(None)
                            .normalize()
                        ],
                    ),
                ]
            ).sort_index()
            a.add(name, series.rename(name), "×")
        a.note = "Portal-KGV und rekonstruiertes Earnings-KGV können abweichen. Forward P/E enthält Schätzungen; EV/EBITDA berücksichtigt Cash-Investitionen nicht."
    elif number == 27:
        cf = cash(bundle)
        fcf = cf[CF[2]].iloc[-1]
        cap, shares = bundle["info"]["marketCap"], bundle["info"]["sharesOutstanding"]
        spot = bundle["prices"]["MSFT"].Close.iloc[-1]
        if not all(np.isfinite(x) and x > 0 for x in (cap, shares, spot, fcf)):
            raise AnalysisUnavailable(
                "Reverse DCF braucht positive, endliche Basiswerte."
            )
        if abs(cap / (shares * spot) - 1) > 0.05:
            raise AnalysisUnavailable(
                "Marktkapitalisierung, Aktienzahl und Kurs weichen um mehr als 5 % ab."
            )
        rates = np.array([0.08, 0.09, 0.10, 0.11, 0.12])
        growths = np.array([0, 0.05, 0.10, 0.15, 0.20, 0.25])
        values = np.array(
            [[equity_value(fcf, g, r) / shares for g in growths] for r in rates]
        )
        diff = (values / spot - 1) * 100
        limit = max(1, np.nanmax(abs(diff)))
        fig = go.Figure(
            go.Heatmap(
                x=growths * 100,
                y=rates * 100,
                z=diff,
                customdata=values,
                colorscale=VALUE_COLORS,
                zmin=-limit,
                zmax=limit,
                zmid=0,
                text=values,
                texttemplate="%{text:.0f}",
                textfont_color="white",
                hovertemplate="Wachstum %{x:.0f}% · Rendite %{y:.0f}%<br>Wert: %{customdata:.2f} USD<br>Abstand zum Kurs: %{z:+.1f}%<extra></extra>",
                colorbar=dict(title="Abstand %"),
            )
        )
        fig.update_layout(
            height=400,
            xaxis_title="FCF-Wachstum p. a. (%)",
            yaxis_title="Geforderte Eigenkapitalrendite (%)",
        )
        a.panels.append(
            Panel(
                "Szenariowert USD je Aktie",
                pd.DataFrame(values, index=rates * 100, columns=growths * 100),
                style_figure(fig),
            )
        )
        a.add(
            "Im Preis impliziertes Wachstum",
            pd.Series(
                [implied_growth(fcf, cap, r) * 100 for r in rates],
                index=rates * 100,
                name="FCF-Wachstum",
            ),
            "%",
        )
        a.note = f"FCF-Basis: GJ {cf.index[-1].year}. Zehn Jahre Wachstum, danach 2,5 %, konstante Aktienzahl. CFO−Capex als vereinfachter Eigenkapital-CF-Proxy, kein FCFF-Modell und keine Owner Earnings. Kein zusätzlicher Nettoschuldenabzug; getrennt von ‚Innerer Wert‘."
    else:
        raise AnalysisUnavailable("Unbekanntes Diagrammthema.")
    return a
