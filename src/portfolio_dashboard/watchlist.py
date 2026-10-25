"""Offline watchlist domain and transactional repository; no holdings or IO providers."""

import json
import sqlite3
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from enum import Enum

from portfolio_dashboard.instruments import EquityForm, VerifiedListing, resolve_listing
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    Position,
    ProviderMapping,
    QuoteUnit,
    _text,
)
from portfolio_dashboard.private_store import PrivateDatabase, PrivateStorageError


class MappingStatus(Enum):
    UNVERIFIED = "unverified"
    COVERAGE_OPEN = "coverage_open"
    VERIFIED = "verified"


@dataclass(frozen=True, slots=True)
class WatchlistEntry:
    """No quantity or cost basis. A label never establishes an identity.

    A VerifiedListing asserts a previous external check, just like the existing
    portfolio/fundamentals contracts. Different share classes use distinct
    verified instrument IDs. Unknown candidates retain only their display label.
    """

    entry_id: str = field(repr=False)
    label: str = field(repr=False)
    listing: VerifiedListing | None = field(default=None, repr=False)
    revision: int = 1

    def __post_init__(self) -> None:
        _text(self.entry_id, "entry_id")
        _text(self.label, "label")
        if self.listing is not None:
            if not isinstance(self.listing, VerifiedListing):
                raise TypeError("listing must be VerifiedListing or None")
            if self.listing.instrument.instrument_type is not InstrumentType.STOCK:
                raise ValueError("watchlist requires a stock listing")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("revision must be a positive integer")

    @property
    def instrument(self) -> Instrument | None:
        return None if self.listing is None else self.listing.instrument

    @property
    def provider_mapping(self) -> ProviderMapping | None:
        return None if self.instrument is None else self.instrument.provider_mapping

    @property
    def mapping_status(self) -> MappingStatus:
        if self.listing is None:
            return MappingStatus.UNVERIFIED
        if self.provider_mapping is None:
            return MappingStatus.COVERAGE_OPEN
        return MappingStatus.VERIFIED

    @property
    def mapping_explanation(self) -> str:
        return {
            MappingStatus.UNVERIFIED: "Instrumentidentität ungeprüft; kein automatischer Providerabruf.",
            MappingStatus.COVERAGE_OPEN: "Instrument geprüft, Providerzuordnung offen; kein automatischer Providerabruf.",
            MappingStatus.VERIFIED: "Instrument und Providerzuordnung ausdrücklich bestätigt.",
        }[self.mapping_status]


class WatchlistConflict(ValueError):
    """Safe domain errors, including optimistic concurrency conflicts."""


def _listing_data(listing: VerifiedListing | None) -> dict | None:
    if listing is None:
        return None
    instrument = listing.instrument
    unit, mapping = instrument.quote_unit, instrument.provider_mapping
    return {
        "instrument": {
            "instrument_type": instrument.instrument_type.value,
            "instrument_id": instrument.instrument_id,
            "display_ticker": instrument.display_ticker,
            "source_symbol": instrument.source_symbol,
            "trading_venue": instrument.trading_venue,
            "source_isin": instrument.source_isin,
            "source_venue": instrument.source_venue,
            "quote_unit": None
            if unit is None
            else {
                "currency": unit.currency,
                "scale_to_currency": None
                if unit.scale_to_currency is None
                else str(unit.scale_to_currency),
            },
            "provider_mapping": None
            if mapping is None
            else {
                "provider": mapping.provider,
                "symbol": mapping.symbol,
            },
        },
        "equity_form": None
        if listing.equity_form is None
        else listing.equity_form.value,
        "isin": listing.isin,
        "wkn": listing.wkn,
        "share_class": listing.share_class,
        "product_id": listing.product_id,
    }


def _object(value: object, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("invalid stored structure")
    return value


def _listing_from_data(value: object) -> VerifiedListing | None:
    if value is None:
        return None
    listing = _object(
        value, {"instrument", "equity_form", "isin", "wkn", "share_class", "product_id"}
    )
    instrument = _object(
        listing["instrument"],
        {
            "instrument_type",
            "instrument_id",
            "display_ticker",
            "source_symbol",
            "trading_venue",
            "source_isin",
            "source_venue",
            "quote_unit",
            "provider_mapping",
        },
    ).copy()
    unit = instrument["quote_unit"]
    if unit is not None:
        unit = _object(unit, {"currency", "scale_to_currency"})
        scale = unit["scale_to_currency"]
        if scale is not None and not isinstance(scale, str):
            raise ValueError("invalid stored scale")
        instrument["quote_unit"] = QuoteUnit(
            unit["currency"], None if scale is None else Decimal(scale)
        )
    mapping = instrument["provider_mapping"]
    if mapping is not None:
        mapping = _object(mapping, {"provider", "symbol"})
        instrument["provider_mapping"] = ProviderMapping(**mapping)
    instrument["instrument_type"] = InstrumentType(instrument["instrument_type"])
    return VerifiedListing(
        instrument=Instrument(**instrument),
        equity_form=None
        if listing["equity_form"] is None
        else EquityForm(listing["equity_form"]),
        **{key: listing[key] for key in ("isin", "wkn", "share_class", "product_id")},
    )


def _payload(entry: WatchlistEntry) -> str:
    return json.dumps(
        {"version": 1, "label": entry.label, "listing": _listing_data(entry.listing)},
        allow_nan=False,
    )


def _entry(row: tuple) -> WatchlistEntry:
    try:
        entry_id, payload, instrument_id, revision = row
        data = _object(json.loads(payload), {"version", "label", "listing"})
        if type(data["version"]) is not int or data["version"] != 1:
            raise ValueError("unknown stored version")
        entry = WatchlistEntry(
            entry_id, data["label"], _listing_from_data(data["listing"]), revision
        )
        expected = None if entry.instrument is None else entry.instrument.instrument_id
        if instrument_id != expected:
            raise ValueError("stored identity mismatch")
        return entry
    except (ValueError, TypeError, KeyError, InvalidOperation):
        raise PrivateStorageError("data") from None


class WatchlistRepository:
    """CRUD with unique confirmed instrument IDs and stale-write protection."""

    def __init__(self, database: PrivateDatabase):
        self._database = database

    def list(self) -> tuple[WatchlistEntry, ...]:
        with self._database.transaction() as connection:
            return tuple(
                _entry(row)
                for row in connection.execute(
                    "SELECT entry_id, payload, instrument_id, revision FROM watchlist ORDER BY entry_id"
                )
            )

    def get(self, entry_id: str) -> WatchlistEntry | None:
        with self._database.transaction() as connection:
            _text(entry_id, "entry_id")
            row = connection.execute(
                "SELECT entry_id, payload, instrument_id, revision FROM watchlist WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()
            return None if row is None else _entry(row)

    def add(self, entry: WatchlistEntry) -> WatchlistEntry:
        with self._database.transaction() as connection:
            if not isinstance(entry, WatchlistEntry) or entry.revision != 1:
                raise ValueError("new entry must be WatchlistEntry at revision 1")
            try:
                connection.execute(
                    "INSERT INTO watchlist (entry_id, payload, instrument_id, revision) VALUES (?, ?, ?, 1)",
                    (
                        entry.entry_id,
                        _payload(entry),
                        None
                        if entry.instrument is None
                        else entry.instrument.instrument_id,
                    ),
                )
            except sqlite3.IntegrityError:
                raise WatchlistConflict(
                    "Eintrag oder bestätigte Aktie bereits in der Watchlist."
                ) from None
            return entry

    def update(self, entry: WatchlistEntry) -> WatchlistEntry:
        with self._database.transaction() as connection:
            if not isinstance(entry, WatchlistEntry):
                raise TypeError("entry must be WatchlistEntry")
            try:
                changed = connection.execute(
                    "UPDATE watchlist SET payload = ?, instrument_id = ?, revision = revision + 1 "
                    "WHERE entry_id = ? AND revision = ?",
                    (
                        _payload(entry),
                        None
                        if entry.instrument is None
                        else entry.instrument.instrument_id,
                        entry.entry_id,
                        entry.revision,
                    ),
                ).rowcount
            except sqlite3.IntegrityError:
                raise WatchlistConflict(
                    "Bestätigte Aktie bereits in der Watchlist."
                ) from None
            if changed != 1:
                raise WatchlistConflict(
                    "Eintrag fehlt oder wurde geändert. Watchlist erneut laden."
                )
            return replace(entry, revision=entry.revision + 1)

    def remove(self, entry_id: str, *, expected_revision: int) -> None:
        with self._database.transaction() as connection:
            _text(entry_id, "entry_id")
            if type(expected_revision) is not int or expected_revision < 1:
                raise ValueError("expected_revision must be a positive integer")
            if (
                connection.execute(
                    "DELETE FROM watchlist WHERE entry_id = ? AND revision = ?",
                    (entry_id, expected_revision),
                ).rowcount
                != 1
            ):
                raise WatchlistConflict(
                    "Eintrag fehlt oder wurde geändert. Watchlist erneut laden."
                )


@dataclass(frozen=True, slots=True)
class WatchlistMembership:
    entry: WatchlistEntry
    in_portfolio: bool
    valuation_key: str | None = field(repr=False)


def link_portfolio(
    entries: tuple[WatchlistEntry, ...],
    positions: tuple[Position, ...],
    listings: tuple[VerifiedListing, ...],
) -> tuple[WatchlistMembership, ...]:
    """Mark exact checked overlap and share a security key, never ticker/issuer.

    This key can be deduplicated before resolving the existing
    FundamentalRegistry. A different class keeps its own key. No positions,
    quantities, costs or report sets are changed or valued by this function.
    """
    if not isinstance(entries, tuple) or not all(
        isinstance(e, WatchlistEntry) for e in entries
    ):
        raise TypeError("entries must be a tuple of WatchlistEntry")
    if not isinstance(positions, tuple) or not all(
        isinstance(p, Position) for p in positions
    ):
        raise TypeError("positions must be a tuple of Position")
    # Validate supplied registry even for an empty portfolio.
    if not isinstance(listings, tuple) or not all(
        isinstance(v, VerifiedListing) for v in listings
    ):
        raise TypeError("listings must be a tuple of VerifiedListing")
    combined = dict()
    for listing in (*listings, *(e.listing for e in entries if e.listing is not None)):
        key = (listing.instrument.instrument_id, listing.instrument.trading_venue)
        if key in combined and combined[key] != listing:
            raise ValueError("conflicting verified listing")
        combined[key] = listing
    registry = tuple(combined.values())
    if registry:
        resolve_listing(registry[0].instrument, registry)
    portfolio_ids = {
        listing.instrument.instrument_id
        for position in positions
        if (listing := resolve_listing(position.instrument, registry)) is not None
    }
    return tuple(
        WatchlistMembership(
            entry,
            entry.instrument is not None
            and entry.instrument.instrument_id in portfolio_ids,
            None if entry.instrument is None else entry.instrument.instrument_id,
        )
        for entry in entries
    )
