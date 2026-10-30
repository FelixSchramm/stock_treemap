"""Streamlit view of the valuation overview «Innerer Wert» (M8-10, #79).

All figures come from ``valuation_overview``; this module only loads on explicit
request (button), keeps the result in the session and draws filters, key figures,
table, compact cards and the status notes. The fundamentals bundle comes from the
shared loading path of #74 (``loader.load``); nothing is retrieved on a plain
rerun. No buy/sell signal is drawn and no missing value is shown as zero.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import streamlit as st

from portfolio_dashboard import valuation_detail, valuation_detail_view
from portfolio_dashboard.fundamentals_loader import (
    FundamentalsBundle,
    FundamentalsLoader,
    FundamentalsStatus,
)
from portfolio_dashboard.instruments import VerifiedListing
from portfolio_dashboard.models import Instrument
from portfolio_dashboard.private_store import PrivateStorageError
from portfolio_dashboard.scenarios import ScenarioDocument
from portfolio_dashboard.valuation_overview import (
    EXCLUDED_EXPLANATION,
    SCOPE_ALL,
    SCOPE_OPTIONS,
    SHOW_ALL,
    SHOW_OPTIONS,
    SORT_BY_NAME,
    SORT_FIELDS,
    EvaluationContext,
    FundamentalsState,
    OverviewRow,
    OverviewSource,
    Status,
    Subject,
    build_rows,
    collect_subjects,
    coverage,
    entry_for,
    filter_rows,
    sort_rows,
    verified_instruments,
)
from portfolio_dashboard.valuations import PortfolioValuation
from portfolio_dashboard.watchlist_quotes import WatchlistEntry

VALUATION_AREA = "Innerer Wert"

SCOPE_KEY = "valuation.scope"
QUERY_KEY = "valuation.query"
SHOW_KEY = "valuation.show"
SORT_FIELD_KEY = "valuation.sort_field"
SORT_ORDER_KEY = "valuation.sort_order"
LAYOUT_KEY = "valuation.layout"
LOAD_BUTTON_KEY = "valuation.load"
DETAIL_SELECT_KEY = "valuation.detail_select"
DETAIL_BUTTON_KEY = "valuation.detail_open"
# Entry to the detail/assumption view (#80): the ``instrument_id`` of the verified
# security the user wants to open, or absent. The view of #80 reads and clears it.
DETAIL_KEY = "valuation.detail_key"
# The security whose detail view is open (set from DETAIL_KEY, which is consumed).
DETAIL_OPEN_KEY = "valuation.detail_current"
# Fundamentals held for the session (``LoadedFundamentals``); #80 calls
# ``reset_loaded_state`` after saving so the next run reads the stored scenarios.
LOADED_STATE_KEY = "valuation.loaded"
WIDGET_KEYS = (
    SCOPE_KEY,
    QUERY_KEY,
    SHOW_KEY,
    SORT_FIELD_KEY,
    SORT_ORDER_KEY,
    LAYOUT_KEY,
)

LAYOUT_TABLE = "Tabelle"
LAYOUT_COMPACT = "Kompakt (Karten)"
LAYOUT_OPTIONS = (LAYOUT_TABLE, LAYOUT_COMPACT)
SORT_DESC = "Absteigend"
SORT_ASC = "Aufsteigend"
SORT_ORDER_OPTIONS = (SORT_ASC, SORT_DESC)

LOAD_LABEL_FIRST = "Bewertungsdaten laden"
LOAD_LABEL_AGAIN = "Fundamentaldaten aktualisieren"
LOAD_SPINNER_TEXT = (
    "Fundamentaldaten werden aus der geschützten Ablage gelesen und, soweit fällig, "
    "abgerufen … Das kann bei mehreren Aktien eine Minute dauern."
)
NOT_CONFIGURED_TEXT = (
    "Es ist keine private Fundamentaldaten-Zuordnung eingerichtet "
    "(PORTFOLIO_FUNDAMENTALS_FILE bzw. [[fundamental]] in den Secrets; siehe "
    "docs/FUNDAMENTALS_CACHE.md). Depot und Watchlist bleiben sichtbar, Modellwerte "
    "gibt es ohne Zuordnung nicht."
)
EXPLAIN_TEXT = (
    "Modellschätzung aus einem FCFF-DCF mit eigenen Annahmen, kein Kauf- oder "
    "Verkaufssignal und kein Konfidenzintervall. Der Modellwert gilt zum Stichtag der "
    "Annahmen und wird nicht auf heute fortgeschrieben; Annahmen-Stichtag und überschrittene "
    "Altersgrenzen der Daten stehen in Status und Datenstand. n. v. = nicht verfügbar; "
    "fehlende Werte erscheinen nie als 0."
)
REFRESH_HINT = (
    "„Aktualisieren“ oben lädt in dieser Ansicht nur die Fundamentaldaten neu (und die "
    "Watchlist-Kurse, falls sie noch nicht geladen sind); vorhandene Kursstände von Depot "
    "und Watchlist werden in deren eigenen Ansichten aktualisiert."
)
MEASURES_TEXT = (
    "Zwei Bezugsgrößen, nicht verwechseln: „Kurs-Abschlag zum Basiswert“ = (1 − Kurs ÷ "
    "Basiswert) und bezieht sich auf den Basiswert; „Basiswert über Kurs“ = (Basiswert ÷ "
    "Kurs − 1) und bezieht sich auf den Kurs. Beispiel: Kurs 80, Basiswert 100 ergibt 20 % "
    "Abschlag und 25 % über Kurs; liegt der Kurs über dem Basiswert, sind beide negativ. "
    "Der Zielpreis ist Basiswert × (1 − persönlicher Sicherheitsabschlag)."
)


markdown_text = valuation_detail.markdown_text


@dataclass(frozen=True, slots=True, repr=False)
class LoadedFundamentals:
    """Result of one explicit load, held in the session until the next one."""

    instruments: frozenset[Instrument]
    bundle: FundamentalsBundle | None
    error: str | None = None  # fixed message of a PrivateStorageError
    documents: Mapping[str, ScenarioDocument | None] = field(default_factory=dict)


def reset_loaded_state(state) -> None:
    """Drop the held fundamentals/scenario state (call after saving scenarios, #80)."""
    state.pop(LOADED_STATE_KEY, None)


def private_repository():
    """Scenario store on the protected database (owner/loopback checked per operation)."""
    from portfolio_dashboard.private_store import PrivateDatabase
    from portfolio_dashboard.scenarios import ScenarioRepository

    return ScenarioRepository(PrivateDatabase())


def private_reader():
    """Scenario reader on the protected store (owner/loopback checked per read)."""
    return private_repository().get


def private_source(listings: tuple[VerifiedListing, ...]) -> OverviewSource:
    """Production wiring: stored scenarios from the private store, system clock.

    The same repository saves new revisions from the detail view (#80).
    """
    from datetime import UTC, datetime

    repository = private_repository()
    return OverviewSource(
        listings,
        repository.get,
        lambda: datetime.now(UTC),
        scenario_saver=repository.save,
    )


def load_fundamentals(
    source: OverviewSource,
    loader: FundamentalsLoader,
    subjects: Sequence[Subject],
    *,
    refresh: bool,
) -> LoadedFundamentals:
    """Load fundamentals and stored scenarios; any storage error becomes a message."""
    instruments = verified_instruments(subjects)
    try:
        bundle = loader.load(instruments, refresh=refresh)
        documents: dict[str, ScenarioDocument | None] = {}
        for subject in subjects:
            entry = entry_for(subject, bundle)
            if (
                subject.key is None
                or entry is None
                or entry.identity is None
                or entry.report_set is None
                or entry.status
                in (FundamentalsStatus.UNMAPPED, FundamentalsStatus.UNSUPPORTED)
            ):
                continue
            documents[subject.key] = source.scenario_reader(entry.identity)
    except PrivateStorageError as exc:
        return LoadedFundamentals(frozenset(instruments), None, str(exc))
    return LoadedFundamentals(frozenset(instruments), bundle, None, documents)


def render_load_button(*, loaded: bool, configured: bool) -> bool:
    """The explicit load/refresh action; nothing is fetched without it (real mode)."""
    if not configured:
        return False
    return st.button(
        LOAD_LABEL_AGAIN if loaded else LOAD_LABEL_FIRST, key=LOAD_BUTTON_KEY
    )


def current_state(
    source: OverviewSource,
    loader: FundamentalsLoader | None,
    subjects: Sequence[Subject],
    *,
    explicit: bool,
    auto: bool,
) -> tuple[FundamentalsState, LoadedFundamentals | None]:
    """Fundamentals of this run: loaded on request (or automatically in the demo)."""
    if loader is None:
        return FundamentalsState(None, False), None
    held: LoadedFundamentals | None = st.session_state.get(LOADED_STATE_KEY)
    wanted = frozenset(verified_instruments(subjects))
    # After a save (#80) the held state was dropped: read the stored scenarios again from
    # the shared loader (cache first, same limits) without another click.
    reloading = bool(st.session_state.pop(valuation_detail_view.RELOAD_KEY, False))
    if (
        explicit
        or reloading
        or (auto and (held is None or not wanted <= held.instruments))
    ):
        with st.spinner(LOAD_SPINNER_TEXT):
            held = load_fundamentals(
                source, loader, subjects, refresh=explicit and held is not None
            )
        st.session_state[LOADED_STATE_KEY] = held
    if held is None:
        return FundamentalsState(None, True), None
    return FundamentalsState(held.bundle, True, held.error), held


# --- drawing ---------------------------------------------------------------------------


def render_filters() -> tuple[str, str, str, str, bool, str]:
    """Scope, search, model-value filter, sort and layout (all with session keys)."""
    st.session_state.setdefault(SCOPE_KEY, SCOPE_ALL)
    st.session_state.setdefault(QUERY_KEY, "")
    st.session_state.setdefault(SHOW_KEY, SHOW_ALL)
    st.session_state.setdefault(SORT_FIELD_KEY, SORT_BY_NAME)
    st.session_state.setdefault(SORT_ORDER_KEY, SORT_ASC)
    st.session_state.setdefault(LAYOUT_KEY, LAYOUT_TABLE)
    scope = st.radio("Bestand", SCOPE_OPTIONS, key=SCOPE_KEY, horizontal=True)
    columns = st.columns((2, 1, 1, 1))
    with columns[0]:
        query = st.text_input("Suche (Name, Ticker, Kategorie)", key=QUERY_KEY)
    with columns[1]:
        show = st.selectbox("Anzeige", SHOW_OPTIONS, key=SHOW_KEY)
    with columns[2]:
        sort_by = st.selectbox("Sortierung", SORT_FIELDS, key=SORT_FIELD_KEY)
    with columns[3]:
        order = st.selectbox("Reihenfolge", SORT_ORDER_OPTIONS, key=SORT_ORDER_KEY)
    layout = st.radio("Darstellung", LAYOUT_OPTIONS, key=LAYOUT_KEY, horizontal=True)
    return scope, query, show, sort_by, order == SORT_DESC, layout


def render_kpis(rows: Sequence[OverviewRow]) -> None:
    """Coverage of the computable stocks relative to the stocks of the filter."""
    figures = coverage(rows)
    columns = st.columns(4)
    with columns[0]:
        st.metric("Betrachtete Aktien", str(figures.considered))
    with columns[1]:
        st.metric(
            "Mit Modellwert",
            figures.coverage_label,
            help="Berechenbare Aktien (mindestens ein Szenariowert) im Verhältnis zu den "
            "im aktuellen Filter betrachteten Aktien.",
        )
    with columns[2]:
        st.metric("Alle drei Szenarien", str(figures.complete))
    with columns[3]:
        st.metric("Ohne Modellwert", str(figures.without))
    st.caption(
        "Abdeckung = Aktien mit mindestens einem Szenariowert ÷ im Filter betrachtete "
        "Aktien (Zuordnung fehlt, Annahmen fehlen oder Modell nicht unterstützt zählen "
        "im Nenner; ETFs und Derivate sind ausgeschlossen)."
    )
    if figures.excluded:
        kinds = ", ".join(
            f"{kind}: {count}" for kind, count in figures.excluded_by_kind
        )
        st.caption(f"Ausgeschlossene Produktarten (nicht gezählt): {kinds}.")


def render_table(rows: Sequence[OverviewRow], layout: str) -> None:
    """Sortable table or wrapping cards; the details never need hover."""
    if layout == LAYOUT_TABLE:
        st.dataframe(
            [row.to_display_dict() for row in rows], hide_index=True, width="stretch"
        )
    blocks = []
    for row in rows:
        head, *lines = row.detail_lines()
        blocks.append(
            f"**{markdown_text(head)}**  \n"
            + "  \n".join(markdown_text(x) for x in lines)
        )
    if layout == LAYOUT_COMPACT:
        st.markdown("\n\n".join(blocks))
        return
    with st.expander(f"Bewertungsdetails ohne Hover ({len(rows)})", expanded=False):
        st.markdown("\n\n".join(blocks))


def render_excluded(rows: Sequence[OverviewRow]) -> None:
    """Excluded product kinds, explained apart from the valued stocks."""
    with st.expander(f"Ausgeschlossene Produktarten ({len(rows)})", expanded=False):
        st.caption(EXCLUDED_EXPLANATION)
        if not rows:
            st.caption("Im aktuellen Filter gibt es keine ausgeschlossenen Einträge.")
        else:
            st.markdown(
                "\n".join(
                    f"- {markdown_text(r.name)} ({markdown_text(r.product)}, {r.membership})"
                    for r in rows
                )
            )


def render_notes(rows: Sequence[OverviewRow], global_notes: Sequence[str]) -> None:
    """Row-referenced reasons for every stock without (full) model value."""
    open_rows = [r for r in rows if r.considered and r.status is not Status.VALUED]
    count = len(open_rows) + len(global_notes)
    with st.expander(f"Datenstatus & Hinweise ({count})", expanded=False):
        if not count:
            st.caption(
                "Alle betrachteten Aktien sind mit allen drei Szenarien bewertet."
            )
        lines = [f"- {markdown_text(n)}" for n in global_notes]
        for row in open_rows:
            reasons = "; ".join(row.reasons) or row.status.value
            lines.append(
                f"- **{markdown_text(row.name)}**: {markdown_text(row.status_text)} – {markdown_text(reasons)}"
            )
        if lines:
            st.markdown("\n".join(lines))


def render_detail_entry(rows: Sequence[OverviewRow]) -> None:
    """Entry to the detail/assumption view (#80): remembers the choice, then opens it."""
    openable = [r for r in rows if r.detail_key is not None]
    st.subheader("Details und Annahmen")
    if not openable:
        st.caption("Für keinen Eintrag im Filter ist eine Detailansicht möglich.")
        return
    labels = [f"{r.name} ({r.membership}) – {r.status_text}" for r in openable]
    chosen = st.selectbox(
        "Aktie für die Detailansicht",
        range(len(openable)),
        format_func=lambda i: labels[i],
        key=DETAIL_SELECT_KEY,
    )
    if st.button("Detail- und Annahmenansicht öffnen", key=DETAIL_BUTTON_KEY):
        st.session_state[DETAIL_KEY] = openable[chosen].detail_key
        st.rerun()


def open_detail_key(state) -> str | None:
    """Read and consume the requested security; it stays open in its own key."""
    requested = state.pop(DETAIL_KEY, None)
    if requested is not None:
        state[DETAIL_OPEN_KEY] = requested
    return state.get(DETAIL_OPEN_KEY)


def close_detail(state) -> None:
    state.pop(DETAIL_OPEN_KEY, None)


def render_overview(
    source: OverviewSource,
    *,
    portfolio: PortfolioValuation | None,
    entries: Sequence[WatchlistEntry],
    state: FundamentalsState,
    loaded: LoadedFundamentals | None,
    stamps: Sequence[str],
) -> None:
    """Draw the overview from already loaded inputs (no retrieval here)."""
    subjects = collect_subjects(portfolio, entries, source.listings)
    context = EvaluationContext(
        source.listings,
        source.clock(),
        state,
        loaded.documents if loaded is not None else {},
        demo=source.demo,
    )
    rows = build_rows(subjects, context)
    opened = open_detail_key(st.session_state)
    if opened is not None:
        row = next((r for r in rows if r.detail_key == opened), None)
        subject = next((s for s in subjects if s.key == opened), None)
        if row is not None and subject is not None:
            valuation_detail_view.render_detail(
                valuation_detail_view.DetailContext(
                    source,
                    row,
                    entry_for(subject, state.bundle),
                    loaded.documents.get(opened) if loaded is not None else None,
                    source.clock(),
                    lambda: reset_loaded_state(st.session_state),
                    lambda: close_detail(st.session_state),
                )
            )
            return
        close_detail(st.session_state)
        valuation_detail_view.clear_drafts()
    st.caption(EXPLAIN_TEXT)
    st.caption(REFRESH_HINT)
    for stamp in stamps:
        st.caption(stamp)
    if state.error:
        st.error(state.error)
    scope, query, show, sort_by, descending, layout = render_filters()
    visible = filter_rows(rows, scope=scope, query=query, show=show)
    ordered = sort_rows(visible, sort_by, descending=descending)
    if not rows:
        st.info("Weder im Depot noch auf der Watchlist gibt es Einträge.")
        return
    render_kpis(visible)
    st.caption(MEASURES_TEXT)
    considered = [r for r in ordered if r.considered]
    st.subheader("Bewertungsübersicht")
    shown = (
        f"{len(considered)} Aktien sichtbar"
        if len(visible) != len(rows)
        else f"Alle {len(considered)} Aktien"
    )
    st.caption(
        f"{shown} · Modellwerte je Aktie in Berichtswährung · Kurs in Handelswährung."
    )
    if considered:
        render_table(considered, layout)
    else:
        st.info("Keine Aktien für die aktuelle Filterauswahl.")
    render_excluded([r for r in ordered if not r.considered])
    notes = [] if state.configured else [NOT_CONFIGURED_TEXT]
    render_notes(ordered, notes)
    render_detail_entry(considered)
