"""Shared CI 1.3 tokens, copied from Financial Pipeline (see EXPLORATION.md)."""

import json
from pathlib import Path

import plotly.graph_objects as go

CI = json.loads(Path(__file__).with_name("ci.json").read_text())
DARK = CI["color"]["neutral"]["dark"]
COLORS = [slot["hex"] for slot in CI["color"]["categorical"]["dark"]]
VALUE_COLORS = CI["color"]["diverging"]["returns_dark"]
UP = CI["color"]["finance"]["dark"]["up"]
DOWN = CI["color"]["finance"]["dark"]["down"]
FONT = CI["typography"]["font_family"]["stack_css"].replace("'", "")


def style_figure(fig: go.Figure) -> go.Figure:
    """Apply the same typography, surface and axes to all dashboard figures."""
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=DARK["surface"],
        plot_bgcolor=DARK["surface"],
        colorway=COLORS,
        font=dict(family=FONT, color=DARK["ink"], size=13),
        hoverlabel=dict(bgcolor=DARK["page"], font=dict(family=FONT, size=13)),
        separators=",.",
        legend=dict(orientation="h", y=1.12, x=0),
        margin=dict(l=16, r=24, t=55, b=35),
    )
    fig.update_xaxes(showgrid=False, linecolor=DARK["axis"], zeroline=False)
    fig.update_yaxes(gridcolor=DARK["gridline"], zerolinecolor=DARK["axis"])
    return fig


def dashboard_css() -> str:
    """Use narrow selectors so controls retain Streamlit focus and accessibility."""
    return f"""<style>
    .stApp {{background: {DARK["page"]}; color: {DARK["ink"]};}}
    h1, h2, h3, [data-testid="stMetricValue"] {{font-family: {FONT};}}
    .stMainBlockContainer {{padding-top: 2rem; padding-bottom: 2rem;}}
    h1 {{font-size: 28px; border-bottom: 2px solid {DARK["forest_on_dark"]}; padding-bottom: 1rem;}}
    [data-testid="stMetric"] {{background: {DARK["surface"]}; border: 1px solid {DARK["border"]};
      border-radius: 12px; padding: 16px; font-variant-numeric: tabular-nums;}}
    [data-testid="stCaptionContainer"] {{color: {DARK["ink_secondary"]};}}
    </style>"""
