"""Valuation overview for portfolio and Watchlist (M8-10, #79); pure and offline.

One row per distinct security. A verified stock is identified only by the
``instrument_id`` of its verified registry listing, so the same security held in
the portfolio and observed on the Watchlist is one row with one set of model
values (``in_portfolio`` and ``on_watchlist`` are flags, never two valuations).
Names, tickers and row numbers are never identities. Rows without a verified
identity, without fundamentals or without assumptions stay visible with their
reason; a missing value is never shown as zero.

Nothing here reads a clock, Sheet, provider, SQLite store or Streamlit: the caller
passes the portfolio valuation, the Watchlist entries, the verified registry, the
fundamentals bundle of the shared loading path (#74), the stored scenario
documents (#76) and the comparison time. Model values come unchanged from
``dcf`` and the price relation from ``price_comparison`` (#77/#78); there is no
buy/sell signal and no default financial assumption.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException
from enum import Enum

from portfolio_dashboard.dcf import calculate_scenarios
from portfolio_dashboard.fundamentals import (
    QualityIssue,
    QualityStatus,
    VerifiedFundamentalIdentity,
)
from portfolio_dashboard.fundamentals_loader import (
    FundamentalsBundle,
    FundamentalsEntry,
    FundamentalsStatus,
)
from portfolio_dashboard.instruments import VerifiedListing, resolve_listing
from portfolio_dashboard.intrinsic_models import Scenario
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    PositionActivity,
    Provenance,
    Valuation,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.price_comparison import (
    ComparisonNote,
    ComparisonPolicy,
    ComparisonReason,
    PriceComparison,
    PricePosition,
    compare_scenarios_to_price,
)
from portfolio_dashboard.refresh import format_snapshot_age
from portfolio_dashboard.scenarios import ScenarioDocument
from portfolio_dashboard.treemap import NOT_AVAILABLE, _german, format_percent
from portfolio_dashboard.valuations import FxRate, PortfolioValuation
from portfolio_dashboard.watchlist import IdentityStatus, link_portfolio
from portfolio_dashboard.watchlist_quotes import (
    WatchlistEntry,
    WatchlistOrigin,
)

# --- filters and sorting ------------------------------------------------------

SCOPE_ALL = "Alle"
SCOPE_PORTFOLIO = "Portfolio"
SCOPE_WATCHLIST = "Watchlist"
SCOPE_OPTIONS = (SCOPE_ALL, SCOPE_PORTFOLIO, SCOPE_WATCHLIST)

SHOW_ALL = "Alle Einträge"
SHOW_VALUED = "Nur mit Modellwert"
SHOW_UNVALUED = "Nur ohne Modellwert"
SHOW_OPTIONS = (SHOW_ALL, SHOW_VALUED, SHOW_UNVALUED)

SORT_BY_NAME = "Name"
SORT_BY_TICKER = "Ticker"
SORT_BY_DISCOUNT = "Abschlag zum Modellwert (Basis)"
SORT_BY_DISTANCE = "Abstand zum Modellwert (Basis)"
SORT_FIELDS = (SORT_BY_NAME, SORT_BY_TICKER, SORT_BY_DISCOUNT, SORT_BY_DISTANCE)

MEMBERSHIP_PORTFOLIO = "Depot"
MEMBERSHIP_WATCHLIST = "Watchlist"
MEMBERSHIP_BOTH = "im Depot und auf Watchlist"

# Technical hint limits for the comparison (docs/PRICE_COMPARISON.md, open owner
# question 1): they mark an old price visibly or block a stale FX rate, they are
# not financial assumptions. The application loads no FX rates yet.
MAX_FX_AGE = timedelta(days=4)
MAX_PRICE_AGE = timedelta(days=4)

NO_VALUE = NOT_AVAILABLE
SCENARIO_LABELS = {
    Scenario.CAUTIOUS: "Vorsichtig",
    Scenario.BASE: "Basis",
    Scenario.OPTIMISTIC: "Optimistisch",
}
SCENARIO_ORDER = (Scenario.CAUTIOUS, Scenario.BASE, Scenario.OPTIMISTIC)

PRODUCT_LABELS = {
    InstrumentType.STOCK: "Aktie",
    InstrumentType.ETF: "ETF",
    InstrumentType.DERIVATIVE: "Derivat",
    None: "Art nicht angegeben",
}
EXCLUDED_EXPLANATION = (
    "Der Innere Wert ist ein FCFF-Modell für einzelne Stammaktien von Nichtfinanz"
    "unternehmen (ADR 0010). ETFs und Derivate haben keinen solchen Wert; sie "
    "werden hier gezeigt, aber weder bewertet noch in der Abdeckung gezählt."
)


class Status(Enum):
    """Why a row has (or lacks) model values; the label is always shown as text."""

    VALUED = "Bewertet"
    PARTIAL = "Teilweise bewertet"
    ASSUMPTIONS_MISSING = "Annahmen fehlen"
    INCOMPLETE = "Daten/Annahmen unvollständig"
    UNCONFIRMED = "Annahmen unbestätigt"
    STALE = "Daten veraltet"
    INVALID = "Angaben ungültig"
    UNSUPPORTED = "Modell nicht unterstützt"
    DATA_CHANGED = "Fundamentaldaten geändert, Annahmen prüfen"
    NO_DATA = "Keine Fundamentaldaten"
    UNMAPPED = "Fundamentaldaten-Zuordnung fehlt"
    NOT_CONFIGURED = "Fundamentaldaten nicht eingerichtet"
    NOT_LOADED = "Fundamentaldaten noch nicht geladen"
    LOAD_FAILED = "Fundamentaldaten nicht verfügbar"
    IDENTITY_MISSING = "Zuordnung fehlt"
    EXCLUDED = "Kein Bewertungsmodell"


_COVERED = frozenset({Status.VALUED, Status.PARTIAL})

_QUALITY_TEXT = {
    QualityStatus.MISSING: "Pflichtangabe fehlt",
    QualityStatus.INVALID: "Angabe ungültig",
    QualityStatus.INCOMPLETE: "unvollständig",
    QualityStatus.STALE: "Daten veraltet",
    QualityStatus.UNCONFIRMED_ASSUMPTIONS: "nicht bestätigt",
    QualityStatus.UNSUPPORTED_MODEL: "Modell nicht unterstützt",
}
_STATUS_FOR_QUALITY = (
    (QualityStatus.UNSUPPORTED_MODEL, Status.UNSUPPORTED),
    (QualityStatus.INVALID, Status.INVALID),
    (QualityStatus.INCOMPLETE, Status.INCOMPLETE),
    (QualityStatus.MISSING, Status.INCOMPLETE),
    (QualityStatus.UNCONFIRMED_ASSUMPTIONS, Status.UNCONFIRMED),
    (QualityStatus.STALE, Status.STALE),
)
_AGE_CODE_TEXT = {
    "annual_report_age": "Jahresabschluss",
    "bridge_age": "Bilanzstichtag der Kapitalbrücke",
    "share_count_age": "Aktienzahl",
}
_LIMITATION_TEXT = {
    "terminal_value_dominant": "Terminalwertanteil ab 75 %: Wert hängt überwiegend vom Restwert ab",
    "negative_forecast_fcff": "negativer FCFF in einem Prognosejahr",
    "scenario_values_not_ordered": "Szenariowerte nicht in der Reihenfolge vorsichtig ≤ Basis ≤ optimistisch",
}
_NOTE_TEXT = {
    ComparisonNote.PRICE_CACHED: "Kurs: letzter gültiger Anbieterstand (zwischengespeichert)",
    ComparisonNote.PRICE_SHEET_REPLACEMENT: "Kurs: Sheet-Wert, kein Anbieterkurs",
    ComparisonNote.PRICE_TIME_UNKNOWN: "Kurszeit unbekannt",
    ComparisonNote.PRICE_OLDER_THAN_LIMIT: f"Kurs älter als {MAX_PRICE_AGE.days} Tage",
    ComparisonNote.FX_CACHED: "Wechselkurs: zwischengespeichert",
    ComparisonNote.COMPARED_VIA_EUR: "Währungen verschieden: Vergleich über EUR",
}
_REASON_TEXT = {
    ComparisonReason.MODEL_VALUE_UNAVAILABLE: "kein Modellwert",
    ComparisonReason.MODEL_VALUATION_AFTER_AS_OF: "Modellstichtag nach dem Vergleichszeitpunkt",
    ComparisonReason.INSTRUMENT_UNVERIFIED: "Kurs gehört zu keinem geprüften Listing",
    ComparisonReason.INSTRUMENT_MISMATCH: "Kurs gehört zu einer anderen Aktie",
    ComparisonReason.ADR_RATIO_UNSUPPORTED: "ADR: Verhältnis zur Aktie nicht bekannt",
    ComparisonReason.NOT_COMMON_SHARE: "keine Stammaktie",
    ComparisonReason.PRICE_MISSING: "Kurs fehlt",
    ComparisonReason.PRICE_INVALID: "Kurs ungültig",
    ComparisonReason.PRICE_UNIT_UNKNOWN: "Kurseinheit unbekannt",
    ComparisonReason.PRICE_PROVIDER_UNVERIFIED: "Kursanbieter nicht geprüft",
    ComparisonReason.PRICE_UNIT_MISMATCH: "Kurseinheit widerspricht dem geprüften Listing",
    ComparisonReason.PRICE_NOT_POSITIVE: "Kurs nicht positiv",
    ComparisonReason.PRICE_TIME_AFTER_AS_OF: "Kurs nach dem Vergleichszeitpunkt abgerufen",
    ComparisonReason.FX_MISSING: "Wechselkurs fehlt",
    ComparisonReason.FX_DIRECTION_MISMATCH: "Wechselkurs in falscher Richtung",
    ComparisonReason.FX_TIME_UNKNOWN: "Wechselkurszeit unbekannt",
    ComparisonReason.FX_STALE: "Wechselkurs zu alt",
    ComparisonReason.FX_TIME_AFTER_AS_OF: "Wechselkurs nach dem Vergleichszeitpunkt",
    ComparisonReason.ARITHMETIC_ERROR: "Rechenfehler im Vergleich",
}
_POSITION_TEXT = {
    PricePosition.BELOW_MODEL_VALUE: "Kurs liegt unter dem Basiswert",
    PricePosition.AT_MODEL_VALUE: "Kurs entspricht dem Basiswert",
    PricePosition.ABOVE_MODEL_VALUE: "Kurs liegt über dem Basiswert",
}
_NOTE_CODE_TEXT = {
    "retrieval_window_expired": "Abruffrist abgelaufen, letzter vollständiger Stand",
    "refresh_failed": "letzte Aktualisierung fehlgeschlagen, früherer Stand",
    "package_version_changed": "Abrufprogramm geändert, früherer Stand",
    "backoff_active": "neuer Abruf wegen früherer Fehler pausiert",
    "manual_refresh_too_soon": "Aktualisierung zu kurz nach dem letzten Abruf",
    "deferred_batch_limit": "Abruf folgt beim nächsten Aktualisieren (Stapelgrenze)",
    "provider_backoff": "Abrufe vorübergehend gesperrt (Anbieterbeschränkung)",
    "report_set_rejected": "Berichtssatz nicht verwertbar",
    "content_unchanged": "Inhalt gegenüber dem letzten Abruf unverändert",
}

DEMO_NOTE = "Demo: Annahmen, manuelle Werte und Zahlen sind erfunden (synthetisch)"
NOT_LOADED_EXPLANATION = (
    "Die Fundamentaldaten sind noch nicht geladen. „Bewertungsdaten laden“ liest sie aus "
    "der geschützten Ablage und ruft sie, soweit fällig, neu ab."
)
NO_ASSUMPTIONS_LINE = "Annahmenstand: keine gespeicherten Annahmen"
IDENTITY_MISSING_PORTFOLIO = (
    "Die Depotposition hat keine geprüfte Zuordnung (Kennung und Handelsplatz) in den "
    "Listings; ohne sie gibt es weder Fundamentaldaten noch einen Modellwert."
)
IDENTITY_MISSING_NO_CANDIDATE = (
    "Im Watchlist-Blatt fehlt eine vollständige Yahoo-Zuordnung (Yahoo-Symbol, "
    "Handelsplatz und Währung) oder sie widerspricht sich; ohne geprüfte Zuordnung "
    "gibt es keinen Modellwert."
)
IDENTITY_MISSING_UNMATCHED = (
    "Die Yahoo-Angabe im Watchlist-Blatt entspricht keinem geprüften Listing "
    "(Symbol, Handelsplatz, Währung). Das Blatt liefert nur einen Kandidaten; das "
    "Listing muss in der geprüften Zuordnung stehen, damit es Fundamentaldaten erhält."
)
UNMAPPED_EXPLANATION = (
    "Das Listing ist geprüft, aber für diese Handelsplatz-Notierung gibt es keinen "
    "Eintrag der privaten Fundamentaldaten-Zuordnung. Je Listing ist ein eigener "
    "Eintrag mit gemeinsamem Quellsymbol nötig (docs/FUNDAMENTALS_CACHE.md)."
)


# --- subjects: portfolio groups and Watchlist rows, merged by verified key --------


@dataclass(frozen=True, slots=True)
class PriceSource:
    """One observed price with the listing instrument it belongs to."""

    valuation: Valuation
    instrument: Instrument | None
    origin: str  # MEMBERSHIP_PORTFOLIO or MEMBERSHIP_WATCHLIST
    source_label: str


@dataclass(frozen=True, slots=True)
class Subject:
    """One security or unverified entry to show; ``key`` only for verified stocks."""

    name: str
    ticker: str | None
    category: str | None
    product: InstrumentType | None
    in_portfolio: bool
    on_watchlist: bool
    key: str | None
    listing_instruments: tuple[Instrument, ...]
    explanation: str | None
    prices: tuple[PriceSource, ...]
    watchlist_rows: int = 0

    @property
    def considered(self) -> bool:
        """Counted in the coverage: not a positively known non-stock product."""
        return self.product in (InstrumentType.STOCK, None)


def _price_label(valuation: Valuation, *, demo: bool = False) -> str:
    source = valuation.provenance.source
    if demo:
        return "Demo-Wert, synthetisch"
    if source is ValuationSource.SHEET:
        return (
            "Sheet-Wert, Kursstand unbekannt"
            if valuation.provenance.observed_at is None
            else "Sheet-Wert"
        )
    if source is ValuationSource.CACHED_PROVIDER:
        return "Anbieter, zwischengespeichert"
    return "Anbieter"


def _entry_price(
    entry: WatchlistEntry, instrument: Instrument | None
) -> PriceSource | None:
    if entry.price is None or entry.price_unit is None:
        return None
    demo = entry.price_origin is WatchlistOrigin.DEMO
    if entry.price_origin is WatchlistOrigin.SHEET:
        valuation = entry.item.sheet_price
        if valuation.status is not ValueStatus.AVAILABLE:
            return None
    elif entry.price_origin in (
        WatchlistOrigin.PROVIDER,
        WatchlistOrigin.CACHED_PROVIDER,
        WatchlistOrigin.DEMO,
    ):
        if entry.retrieved_at is None:
            return None
        cached = entry.price_origin is WatchlistOrigin.CACHED_PROVIDER
        valuation = Valuation(
            entry.price,
            ValueStatus.AVAILABLE,
            Provenance(
                ValuationSource.CACHED_PROVIDER if cached else ValuationSource.PROVIDER,
                entry.retrieved_at,
                None,
                "yfinance",
            ),
            entry.price_unit,
        )
    else:
        return None
    return PriceSource(
        valuation, instrument, MEMBERSHIP_WATCHLIST, _price_label(valuation, demo=demo)
    )


def collect_subjects(
    portfolio: PortfolioValuation | None,
    entries: Sequence[WatchlistEntry],
    listings: tuple[VerifiedListing, ...],
) -> tuple[Subject, ...]:
    """Merge active portfolio groups and Watchlist rows into distinct subjects.

    Verified stocks with the same ``instrument_id`` become one subject (portfolio
    first, then Watchlist rows of securities not yet present); every other entry
    stays its own subject so nothing is dropped. No quantity or cost basis is read.
    """
    order: list[object] = []  # str key or int placeholder index
    merged: dict[str, dict] = {}
    singles: list[dict] = []

    def add(draft: dict) -> None:
        key = draft["key"]
        if key is None:
            singles.append(draft)
            order.append(len(singles) - 1)
            return
        if key not in merged:
            merged[key] = draft
            order.append(key)
            return
        held = merged[key]
        held["on_watchlist"] = held["on_watchlist"] or draft["on_watchlist"]
        held["in_portfolio"] = held["in_portfolio"] or draft["in_portfolio"]
        held["instruments"] = tuple(
            dict.fromkeys(held["instruments"] + draft["instruments"])
        )
        held["prices"] = held["prices"] + draft["prices"]
        held["rows"] += draft["rows"]

    active_positions = []
    for group in () if portfolio is None else portfolio.groups:
        members = [
            m for m in group.members if m.position.activity is PositionActivity.ACTIVE
        ]
        if not members:
            continue
        active_positions.extend(m.position for m in members)
        first = members[0].position
        listing = resolve_listing(first.instrument, listings)
        instruments: tuple[Instrument, ...] = ()
        key = None
        product = first.instrument.instrument_type
        if listing is not None:
            product = listing.instrument.instrument_type
            if product is InstrumentType.STOCK:
                key = listing.instrument.instrument_id
                found = [listing.instrument]
                for member in members[1:]:
                    other = resolve_listing(member.position.instrument, listings)
                    if other is not None:
                        found.append(other.instrument)
                instruments = tuple(dict.fromkeys(found))
        price = None
        for member in members:
            valuation = member.position.valuation
            if (
                valuation is not None
                and valuation.status is ValueStatus.AVAILABLE
                and valuation.unit is not None
            ):
                price = PriceSource(
                    valuation,
                    member.position.instrument,
                    MEMBERSHIP_PORTFOLIO,
                    _price_label(valuation),
                )
                break
        add(
            {
                "name": first.name,
                "ticker": first.instrument.display_ticker,
                "category": first.category,
                "product": product,
                "in_portfolio": True,
                "on_watchlist": False,
                "key": key,
                "instruments": instruments,
                "explanation": (
                    IDENTITY_MISSING_PORTFOLIO
                    if key is None
                    and product is InstrumentType.STOCK
                    and listing is None
                    else None
                ),
                "prices": () if price is None else (price,),
                "rows": 0,
            }
        )

    memberships = link_portfolio(
        tuple(e.item for e in entries), tuple(active_positions), listings
    )
    for entry, membership in zip(entries, memberships, strict=True):
        item = entry.item
        key = membership.valuation_key
        product = (
            membership.listing.instrument.instrument_type
            if membership.listing is not None
            else item.instrument_type
        )
        explanation = None
        if key is None and product in (InstrumentType.STOCK, None):
            explanation = (
                IDENTITY_MISSING_UNMATCHED
                if membership.status is IdentityStatus.UNMATCHED
                else IDENTITY_MISSING_NO_CANDIDATE
            )
        instrument = membership.instrument
        price = _entry_price(entry, instrument)
        add(
            {
                "name": item.name,
                "ticker": item.ticker,
                "category": item.category,
                "product": product,
                "in_portfolio": membership.in_portfolio,
                "on_watchlist": True,
                "key": key,
                "instruments": () if key is None else (instrument,),
                "explanation": explanation,
                "prices": () if price is None else (price,),
                "rows": 1,
            }
        )

    subjects = []
    for slot in order:
        draft = merged[slot] if isinstance(slot, str) else singles[slot]
        subjects.append(
            Subject(
                draft["name"],
                draft["ticker"],
                draft["category"],
                draft["product"],
                draft["in_portfolio"],
                draft["on_watchlist"],
                draft["key"],
                draft["instruments"],
                draft["explanation"],
                draft["prices"],
                draft["rows"],
            )
        )
    return tuple(subjects)


def verified_instruments(subjects: Sequence[Subject]) -> tuple[Instrument, ...]:
    """Listing instruments to hand to ``FundamentalsLoader.load`` (deduplicated)."""
    return tuple(dict.fromkeys(i for s in subjects for i in s.listing_instruments))


@dataclass(frozen=True, slots=True, repr=False)
class OverviewSource:
    """What the application wiring hands to the view (real or demo).

    ``scenario_reader`` returns the stored scenario document of a verified identity
    (``ScenarioRepository.get`` in production) or ``None``. ``portfolio`` replaces
    the loaded depot valuation (demo only); ``demo`` marks all data as invented.
    ``scenario_saver`` stores a new revision (``ScenarioRepository.save``: owner check,
    versioning and conflict as there); without it the detail view (#80) cannot save.
    """

    listings: tuple[VerifiedListing, ...]
    scenario_reader: Callable[[VerifiedFundamentalIdentity], ScenarioDocument | None]
    clock: Callable[[], datetime]
    portfolio: PortfolioValuation | None = None
    demo: bool = False
    scenario_saver: Callable[[ScenarioDocument], ScenarioDocument] | None = None


# --- evaluation inputs -------------------------------------------------------------


@dataclass(frozen=True, slots=True, repr=False)
class FundamentalsState:
    """What the shared loading path delivered (#74)."""

    bundle: FundamentalsBundle | None = None
    configured: bool = True  # False: no private fundamentals mapping at all
    error: str | None = None  # fixed storage message when loading failed


@dataclass(frozen=True, slots=True, repr=False)
class EvaluationContext:
    """Everything besides the subjects; ``as_of`` is the explicit comparison time."""

    listings: tuple[VerifiedListing, ...]
    as_of: datetime
    fundamentals: FundamentalsState
    documents: Mapping[str, ScenarioDocument | None]
    fx_rates: Mapping[str, FxRate] | None = None
    demo: bool = False


# --- rows ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScenarioView:
    scenario: Scenario
    label: str
    value: Decimal | None
    currency: str | None
    value_label: str
    reason: str | None  # why there is no value
    comparison: PriceComparison | None = None

    @property
    def discount_label(self) -> str:
        c = self.comparison
        return format_percent(None if c is None else c.discount_to_model_percent)

    @property
    def distance_label(self) -> str:
        c = self.comparison
        return format_percent(None if c is None else c.distance_to_model_percent)


@dataclass(frozen=True, slots=True, repr=False)
class OverviewRow:
    index: int
    key: str | None
    detail_key: str | None  # set when the detail view (#80) can open this security
    name: str
    ticker: str | None
    category: str | None
    in_portfolio: bool
    on_watchlist: bool
    product: str
    considered: bool
    status: Status
    status_text: str
    reasons: tuple[str, ...]
    notes: tuple[str, ...]
    price_label: str
    price_source: str
    scenarios: tuple[ScenarioView, ...]
    target_label: str
    margin_label: str
    comparison_text: str
    position_text: str
    stand_short: str
    stand_lines: tuple[str, ...]

    @property
    def membership(self) -> str:
        if self.in_portfolio and self.on_watchlist:
            return MEMBERSHIP_BOTH
        return MEMBERSHIP_PORTFOLIO if self.in_portfolio else MEMBERSHIP_WATCHLIST

    @property
    def covered(self) -> bool:
        return self.considered and self.status in _COVERED

    @property
    def base(self) -> ScenarioView:
        return next(s for s in self.scenarios if s.scenario is Scenario.BASE)

    @property
    def has_model_value(self) -> bool:
        return any(s.value is not None for s in self.scenarios)

    @property
    def discount(self) -> Decimal | None:
        c = self.base.comparison
        return None if c is None else c.discount_to_model_percent

    @property
    def distance(self) -> Decimal | None:
        c = self.base.comparison
        return None if c is None else c.distance_to_model_percent

    def to_display_dict(self) -> dict[str, str]:
        """Table columns; every cell is plain text, scenario gaps are 'n. v.'."""
        return {
            "Name": self.name,
            "Ticker": self.ticker or NO_VALUE,
            "Bestand": self.membership,
            "Kurs": self.price_label,
            "Vorsichtig": self.scenarios[0].value_label,
            "Basis": self.scenarios[1].value_label,
            "Optimistisch": self.scenarios[2].value_label,
            "Kurs-Abschlag zum Basiswert (% des Basiswerts)": self.base.discount_label,
            "Basiswert über Kurs (% des Kurses)": self.base.distance_label,
            "Zielpreis (Basis)": self.target_label,
            "Status": self.status_text,
            "Datenstand": self.stand_short,
        }

    def detail_lines(self) -> tuple[str, ...]:
        """All fields as wrapping text; the first line is the heading."""
        head = self.name + (f" ({self.ticker})" if self.ticker else "")
        lines = [
            head,
            f"Bestand: {self.membership}",
            f"Produktart: {self.product}",
            f"Kategorie: {self.category or 'Ohne Kategorie'}",
            f"Status: {self.status_text}",
            f"Kurs: {self.price_label}"
            + (f" ({self.price_source})" if self.price_source else ""),
        ]
        for view in self.scenarios:
            part = f"Modellwert {view.label}: {view.value_label}"
            if view.value is None and view.reason:
                part += f" ({view.reason})"
            elif view.comparison is not None and view.comparison.comparable:
                part += (
                    f" · Kurs-Abschlag zum Modellwert {view.discount_label}"
                    f" · Modellwert über Kurs {view.distance_label}"
                )
            lines.append(part)
        lines.append(f"Kursvergleich (Basis): {self.comparison_text}")
        if self.position_text:
            lines.append(self.position_text)
        lines.append(f"Persönlicher Zielpreis (Basis): {self.target_label}")
        lines.append(f"Sicherheitsabschlag (Basis): {self.margin_label}")
        lines.extend(self.stand_lines)
        lines.extend(f"Grund: {reason}" for reason in self.reasons)
        lines.extend(f"Hinweis: {note}" for note in self.notes)
        return tuple(lines)


def format_amount(value: Decimal | None, currency: str | None) -> str:
    """Per-share amount in German notation; a missing value is 'n. v.', never zero."""
    if value is None or currency is None:
        return NO_VALUE
    return f"{_german(value, 2)} {currency}"


def _price_view(source: PriceSource | None) -> tuple[str, str]:
    if source is None:
        return NO_VALUE, ""
    unit = source.valuation.unit
    try:
        major = source.valuation.price * unit.scale_to_currency
    except (DecimalException, TypeError):
        return NO_VALUE, ""
    return format_amount(major, unit.currency), source.source_label


def _issue_groups(issues: Sequence[QualityIssue]) -> list[str]:
    """Blocking issues grouped by kind: 'unvollständig (a, b); nicht bestätigt (c)'."""
    grouped: dict[QualityStatus, list[str]] = {}
    for issue in issues:
        if issue.blocking:
            grouped.setdefault(issue.status, []).append(issue.code)
    parts = []
    for status, text in _QUALITY_TEXT.items():
        codes = grouped.get(status)
        if codes:
            parts.append(f"{text} ({', '.join(dict.fromkeys(codes))})")
    return parts


def _issue_notes(issues: Sequence[QualityIssue]) -> list[str]:
    """Non-blocking and age facts as text; also readable without color."""
    manual, book, unknown, stale = [], [], False, []
    for issue in issues:
        if issue.status is QualityStatus.MANUALLY_SUPPLEMENTED:
            manual.append(issue.code)
        elif issue.status is QualityStatus.BOOK_VALUE_APPROXIMATION:
            book.append(issue.code)
        elif issue.status is QualityStatus.UNKNOWN_PUBLICATION:
            unknown = True
        elif issue.status is QualityStatus.STALE:
            stale.append(_AGE_CODE_TEXT.get(issue.code, issue.code))
    notes = []
    if manual:
        notes.append(f"manuell ergänzt: {', '.join(dict.fromkeys(manual))}")
    if book:
        notes.append(f"Buchwert als Näherung: {', '.join(dict.fromkeys(book))}")
    if unknown:
        notes.append("Veröffentlichungsdatum einzelner Angaben unbekannt")
    if stale:
        notes.append(f"älter als die Altersgrenze: {', '.join(dict.fromkeys(stale))}")
    return notes


def _status_for(issues: Sequence[QualityIssue]) -> Status:
    present = {i.status for i in issues if i.blocking}
    for quality, status in _STATUS_FOR_QUALITY:
        if quality in present:
            return status
    return Status.INVALID


def _fmt_date(value) -> str:
    return value.strftime("%d.%m.%Y")


def _stand(
    entry: FundamentalsEntry | None, as_of: datetime, assumptions: str | None
) -> tuple[str, tuple[str, ...]]:
    """Retrieval age and business age, kept apart, plus the assumptions stand."""
    if entry is None or entry.last_checked_at is None:
        short = "nicht abgerufen"
        lines = ["Datenstand Fundamentaldaten: kein vollständiger Abruf vorhanden"]
    else:
        retrieved = (
            f"Abruf {entry.last_checked_at.strftime('%d.%m.%Y, %H:%M %Z')} "
            f"({format_snapshot_age(entry.last_checked_at, as_of)})"
        )
        period_end = entry.latest_period_end
        if period_end is None:
            business = "Geschäftsjahr unbekannt"
            business_long = "Jahresabschluss: Stichtag unbekannt"
        else:
            days = (as_of.date() - period_end).days
            business = f"Abschluss bis {_fmt_date(period_end)}"
            business_long = (
                f"Jahresabschluss bis {_fmt_date(period_end)} "
                f"(fachliches Alter {days} Tage)"
            )
        age = format_snapshot_age(entry.last_checked_at, as_of)
        short = f"Abruf {age}; {business}"
        lines = [f"Abrufalter: {retrieved}", f"Fachliches Alter: {business_long}"]
        if entry.bridge_date is not None:
            lines.append(
                f"Bilanzstichtag Kapitalbrücke: {_fmt_date(entry.bridge_date)}"
            )
        if entry.report_set is not None:
            lines.append(
                f"Aktienzahl-Stichtag: {_fmt_date(entry.report_set.shares_period.as_of)}"
            )
        if entry.retrieved_at is not None:
            lines.append(
                f"Inhalt geliefert: {entry.retrieved_at.strftime('%d.%m.%Y, %H:%M %Z')}"
            )
    if assumptions is not None:
        lines.append(assumptions)
    return short, tuple(lines)


def entry_for(
    subject: Subject, bundle: FundamentalsBundle | None
) -> FundamentalsEntry | None:
    """First listing of the security that the loader can resolve, else the first."""
    if bundle is None:
        return None
    found = [
        bundle.entries[i] for i in subject.listing_instruments if i in bundle.entries
    ]
    if not found:
        return None
    return next(
        (e for e in found if e.status is not FundamentalsStatus.UNMAPPED), found[0]
    )


def _empty_views(reason: str | None) -> tuple[ScenarioView, ...]:
    return tuple(
        ScenarioView(s, SCENARIO_LABELS[s], None, None, NO_VALUE, reason)
        for s in SCENARIO_ORDER
    )


def _row(
    index: int,
    subject: Subject,
    status: Status,
    reasons: Sequence[str],
    notes: Sequence[str],
    views: tuple[ScenarioView, ...],
    stand: tuple[str, tuple[str, ...]],
    price: PriceSource | None,
    *,
    detail_key: str | None = None,
    comparison_text: str = "kein Modellwert, kein Vergleich",
    position_text: str = "",
    target: Decimal | None = None,
    target_currency: str | None = None,
    margin: Decimal | None = None,
    text: str | None = None,
) -> OverviewRow:
    price_label, price_source = _price_view(price)
    return OverviewRow(
        index,
        subject.key,
        detail_key,
        subject.name,
        subject.ticker,
        subject.category,
        subject.in_portfolio,
        subject.on_watchlist,
        PRODUCT_LABELS[subject.product],
        subject.considered,
        status,
        text or status.value,
        tuple(dict.fromkeys(reasons)),
        tuple(dict.fromkeys(notes)),
        price_label,
        price_source,
        views,
        format_amount(target, target_currency),
        NO_VALUE if margin is None else f"{_german(margin * 100, 2)} %",
        comparison_text,
        position_text,
        stand[0],
        stand[1],
    )


def _membership_note(subject: Subject) -> list[str]:
    notes = []
    if subject.watchlist_rows > 1:
        notes.append(
            f"{subject.watchlist_rows} Watchlist-Zeilen derselben Aktie zusammengefasst"
        )
    return notes


def _evaluate(index: int, subject: Subject, context: EvaluationContext) -> OverviewRow:
    as_of = context.as_of
    price = subject.prices[0] if subject.prices else None
    no_stand = _stand(None, as_of, None)
    base_notes = _membership_note(subject)

    if not subject.considered:
        return _row(
            index,
            subject,
            Status.EXCLUDED,
            [f"{PRODUCT_LABELS[subject.product]}: kein FCFF-Bewertungsmodell"],
            base_notes,
            _empty_views(None),
            no_stand,
            price,
            text=f"Kein Bewertungsmodell ({PRODUCT_LABELS[subject.product]})",
        )
    if subject.key is None:
        return _row(
            index,
            subject,
            Status.IDENTITY_MISSING,
            [subject.explanation or IDENTITY_MISSING_NO_CANDIDATE],
            base_notes,
            _empty_views("Zuordnung fehlt"),
            no_stand,
            price,
        )

    state = context.fundamentals
    if not state.configured:
        return _row(
            index,
            subject,
            Status.NOT_CONFIGURED,
            ["Es ist keine private Fundamentaldaten-Zuordnung eingerichtet."],
            base_notes,
            _empty_views("Fundamentaldaten nicht eingerichtet"),
            no_stand,
            price,
        )
    if state.bundle is None:
        failed = state.error is not None
        return _row(
            index,
            subject,
            Status.LOAD_FAILED if failed else Status.NOT_LOADED,
            [state.error or NOT_LOADED_EXPLANATION],
            base_notes,
            _empty_views(
                "Fundamentaldaten nicht verfügbar"
                if failed
                else "Fundamentaldaten noch nicht geladen"
            ),
            no_stand,
            price,
        )
    entry = entry_for(subject, state.bundle)
    if entry is None:
        return _row(
            index,
            subject,
            Status.NOT_LOADED,
            [NOT_LOADED_EXPLANATION],
            base_notes,
            _empty_views("Fundamentaldaten noch nicht geladen"),
            no_stand,
            price,
        )
    if entry.status is FundamentalsStatus.UNMAPPED:
        return _row(
            index,
            subject,
            Status.UNMAPPED,
            [UNMAPPED_EXPLANATION],
            base_notes,
            _empty_views("Zuordnung der Fundamentaldaten fehlt"),
            no_stand,
            price,
        )
    entry_notes = [_NOTE_CODE_TEXT.get(n, n) for n in entry.notes]
    detail_key = subject.key
    if entry.status is FundamentalsStatus.UNSUPPORTED:
        return _row(
            index,
            subject,
            Status.UNSUPPORTED,
            [f"Modell nicht unterstützt ({', '.join(entry.notes) or 'Emittentenart'})"],
            base_notes,
            _empty_views("Modell nicht unterstützt"),
            no_stand,
            price,
            detail_key=detail_key,
        )
    if entry.status is FundamentalsStatus.UNAVAILABLE or entry.report_set is None:
        reasons = [
            "Es liegt kein vollständiger, verwertbarer Abruf der Fundamentaldaten vor."
        ]
        if entry.failures:
            reasons.append(f"Letzter Fehlversuch: {', '.join(entry.failures)}")
        issues = entry.issues_at(as_of) if entry.normalized is not None else ()
        reasons.extend(_issue_groups(issues))
        return _row(
            index,
            subject,
            Status.NO_DATA,
            reasons,
            base_notes + entry_notes,
            _empty_views("Fundamentaldaten fehlen"),
            _stand(entry, as_of, NO_ASSUMPTIONS_LINE),
            price,
            detail_key=detail_key,
        )

    document = context.documents.get(subject.key)
    stand_without = _stand(entry, as_of, NO_ASSUMPTIONS_LINE)
    notes = base_notes + entry_notes
    notes.extend(_issue_notes(entry.report_set.issues_at(as_of)))
    if document is None or not document.scenarios:
        return _row(
            index,
            subject,
            Status.ASSUMPTIONS_MISSING,
            [
                "Für diese Aktie sind keine Szenarioannahmen gespeichert; es wird kein Wert erfunden."
            ],
            notes,
            _empty_views("Annahmen fehlen"),
            stand_without,
            price,
            detail_key=detail_key,
        )
    if document.originals.input_version != entry.report_set.input_version:
        return _row(
            index,
            subject,
            Status.DATA_CHANGED,
            [
                "Die Fundamentaldaten haben sich seit der Erfassung der Annahmen geändert; "
                "die gespeicherten Annahmen gelten nicht für den neuen Berichtssatz."
            ],
            notes,
            _empty_views("Annahmen prüfen"),
            stand_without,
            price,
            detail_key=detail_key,
        )

    assumptions = tuple(s.assumptions for s in document.scenarios)
    valuation_set = calculate_scenarios(document.effective_report_set(), assumptions)
    price_instrument = (
        price.instrument
        if price is not None and price.instrument
        else subject.listing_instruments[0]
    )
    comparisons = compare_scenarios_to_price(
        valuation_set,
        price_instrument,
        None if price is None else price.valuation,
        context.listings,
        context.fx_rates,
        ComparisonPolicy(as_of, MAX_FX_AGE, MAX_PRICE_AGE),
    )
    views = []
    blocking: list[QualityIssue] = []
    all_issues: list[QualityIssue] = []
    for scenario in SCENARIO_ORDER:
        calculation = valuation_set.get(scenario)
        if calculation is None:
            views.append(
                ScenarioView(
                    scenario,
                    SCENARIO_LABELS[scenario],
                    None,
                    None,
                    NO_VALUE,
                    "Annahmen fehlen",
                )
            )
            continue
        result = calculation.result
        extra = [
            i
            for i in document.issues_for(scenario)
            if i.blocking and i not in result.issues
        ]
        all_issues.extend(result.issues)
        if extra or result.value_per_share is None:
            found = [i for i in result.issues if i.blocking] + extra
            blocking.extend(found)
            views.append(
                ScenarioView(
                    scenario,
                    SCENARIO_LABELS[scenario],
                    None,
                    result.currency,
                    NO_VALUE,
                    "; ".join(_issue_groups(found)) or "gesperrt",
                )
            )
            continue
        views.append(
            ScenarioView(
                scenario,
                SCENARIO_LABELS[scenario],
                result.value_per_share,
                result.currency,
                format_amount(result.value_per_share, result.currency),
                None,
                comparisons.get(scenario),
            )
        )
    valued = [v for v in views if v.value is not None]
    if valued:
        status = Status.VALUED if len(valued) == 3 else Status.PARTIAL
        text = (
            status.value
            if status is Status.VALUED
            else f"Teilweise bewertet ({len(valued)} von 3 Szenarien)"
        )
    else:
        status = _status_for(blocking) if blocking else Status.ASSUMPTIONS_MISSING
        text = None
    reasons = []
    for view in views:
        if view.value is None and view.reason:
            reasons.append(f"{view.label}: {view.reason}")
    notes = notes + _issue_notes(all_issues)
    for limitation in dict.fromkeys(
        valuation_set.limitations
        + tuple(code for c in valuation_set.calculations for code in c.limitations)
    ):
        if limitation in _LIMITATION_TEXT:
            notes.append(_LIMITATION_TEXT[limitation])
    if context.demo:
        notes.append(DEMO_NOTE)
    base_cmp = next(v for v in views if v.scenario is Scenario.BASE).comparison
    comparison_text = "kein Basiswert, kein Vergleich"
    position_text = ""
    if base_cmp is not None:
        notes.extend(_NOTE_TEXT[n] for n in base_cmp.notes if n in _NOTE_TEXT)
        if base_cmp.comparable:
            comparison_text = (
                f"Kurs {format_amount(base_cmp.compared_price, base_cmp.comparison_currency)} "
                f"gegen Basiswert {format_amount(base_cmp.compared_model_value, base_cmp.comparison_currency)}: "
                f"Kurs-Abschlag zum Modellwert {format_percent(base_cmp.discount_to_model_percent)} "
                f"(bezogen auf den Modellwert), Modellwert über Kurs "
                f"{format_percent(base_cmp.distance_to_model_percent)} (bezogen auf den Kurs)"
            )
            position_text = _POSITION_TEXT[base_cmp.position]
        else:
            comparison_text = "Kein Kursvergleich: " + ", ".join(
                _REASON_TEXT[r] for r in base_cmp.comparison_reasons
            )
    base_scenario = document.scenario(Scenario.BASE)
    target = margin = None
    target_currency = None
    if base_cmp is not None and base_cmp.target_price is not None:
        target, target_currency = base_cmp.target_price, base_cmp.reporting_currency
    if base_scenario is not None:
        margin = base_scenario.assumptions.safety_margin
    base_assumptions = (
        base_scenario.assumptions if base_scenario is not None else assumptions[0]
    )
    confirmed = base_assumptions.confirmed_at
    assumption_line = (
        f"Annahmenstand: Version {base_assumptions.version}, Stichtag "
        f"{_fmt_date(base_assumptions.valuation_at)}, "
        + (f"bestätigt am {_fmt_date(confirmed)}" if confirmed else "nicht bestätigt")
        + (" (synthetisch)" if context.demo else "")
    )
    stand = _stand(entry, as_of, assumption_line)
    return _row(
        index,
        subject,
        status,
        reasons,
        notes,
        tuple(views),
        stand,
        price,
        detail_key=detail_key,
        comparison_text=comparison_text,
        position_text=position_text,
        target=target,
        target_currency=target_currency,
        margin=margin,
        text=text,
    )


def build_rows(
    subjects: Sequence[Subject], context: EvaluationContext
) -> tuple[OverviewRow, ...]:
    """One row per subject in subject order; none is hidden or reordered."""
    return tuple(_evaluate(i, s, context) for i, s in enumerate(subjects))


# --- filter, sort, coverage -----------------------------------------------------------


def matches(
    row: OverviewRow,
    *,
    scope: str = SCOPE_ALL,
    query: str = "",
    show: str = SHOW_ALL,
) -> bool:
    """Scope, search and model-value filter; they never change a row's valuation."""
    if scope == SCOPE_PORTFOLIO and not row.in_portfolio:
        return False
    if scope == SCOPE_WATCHLIST and not row.on_watchlist:
        return False
    needle = query.strip().casefold()
    if needle and not any(
        needle in (text or "").casefold()
        for text in (row.name, row.ticker, row.category)
    ):
        return False
    if show == SHOW_VALUED and not row.has_model_value:
        return False
    if show == SHOW_UNVALUED and row.has_model_value:
        return False
    return True


def filter_rows(rows: Sequence[OverviewRow], **criteria) -> tuple[OverviewRow, ...]:
    return tuple(r for r in rows if matches(r, **criteria))


def sort_rows(
    rows: Sequence[OverviewRow],
    sort_by: str = SORT_BY_NAME,
    *,
    descending: bool = False,
) -> tuple[OverviewRow, ...]:
    """Sort; rows without a figure always come last (never as zero)."""
    if sort_by not in SORT_FIELDS:
        raise ValueError(f"unsupported sort_by field: {sort_by}")
    tie = lambda r: (r.name.casefold(), r.index)  # noqa: E731
    if sort_by in (SORT_BY_DISCOUNT, SORT_BY_DISTANCE):
        get = (
            (lambda r: r.discount)
            if sort_by == SORT_BY_DISCOUNT
            else (lambda r: r.distance)
        )
        present = [r for r in rows if get(r) is not None]
        missing = sorted((r for r in rows if get(r) is None), key=tie)
        present.sort(key=lambda r: (-get(r) if descending else get(r), *tie(r)))
        return tuple(present + missing)
    if sort_by == SORT_BY_TICKER:
        present = sorted((r for r in rows if r.ticker), key=tie)
        present.sort(key=lambda r: r.ticker.casefold(), reverse=descending)
        return tuple(present + sorted((r for r in rows if not r.ticker), key=tie))
    return tuple(sorted(rows, key=tie, reverse=descending))


@dataclass(frozen=True, slots=True)
class Coverage:
    """Coverage of the rows in the current filter; excluded products are not counted."""

    considered: int
    valued: int  # at least one scenario value
    complete: int  # all three scenario values
    compared: int  # base scenario with a price relation
    without: int
    excluded: int
    excluded_by_kind: tuple[tuple[str, int], ...]

    @property
    def coverage_label(self) -> str:
        if not self.considered:
            return NO_VALUE
        percent = _german(Decimal(self.valued) * 100 / Decimal(self.considered), 0)
        return f"{self.valued} von {self.considered} ({percent} %)"


def coverage(rows: Sequence[OverviewRow]) -> Coverage:
    """Computable stocks relative to the stocks considered in this filter."""
    considered = [r for r in rows if r.considered]
    excluded: dict[str, int] = {}
    for row in rows:
        if not row.considered:
            excluded[row.product] = excluded.get(row.product, 0) + 1
    valued = [r for r in considered if r.covered]
    return Coverage(
        len(considered),
        len(valued),
        sum(r.status is Status.VALUED for r in considered),
        sum(r.discount is not None for r in considered),
        len(considered) - len(valued),
        sum(excluded.values()),
        tuple(sorted(excluded.items())),
    )
