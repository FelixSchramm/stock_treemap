"""Watchlist membership from the Sheet's Watchlist tab (ADR 0011, #95); pure and offline.

The Sheet tab is the only watchlist: nothing is stored, added or removed here.
A parsed ``WatchlistItem`` carries only a Sheet-maintained Yahoo *candidate*.
It receives a valuation key solely by exactly matching an already verified
registry listing (symbol, venue, currency); names, tickers and row numbers never
establish an identity. No IO, provider, Sheet, SQLite or holdings access.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from portfolio_dashboard.instruments import VerifiedListing, resolve_listing
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    Position,
    ProviderMapping,
)
from portfolio_dashboard.watchlist_sheet import WatchlistItem, WatchlistRowStatus

PROVIDER = "yfinance"


class IdentityStatus(Enum):
    VERIFIED = "verified"  # exact match with a verified stock listing: has a key
    VERIFIED_NOT_STOCK = "verified_not_stock"  # verified, but no valuation model
    UNMATCHED = "unmatched"  # candidate absent from the registry or contradictory
    NO_CANDIDATE = "no_candidate"  # no complete Yahoo candidate in the row


_EXPLANATIONS = {
    IdentityStatus.VERIFIED: "Instrument anhand der geprüften Zuordnung bestätigt.",
    IdentityStatus.VERIFIED_NOT_STOCK: (
        "Instrument geprüft, aber keine Aktie; kein Bewertungsschlüssel."
    ),
    IdentityStatus.UNMATCHED: (
        "Yahoo-Angabe stimmt mit keiner geprüften Zuordnung überein; "
        "Instrumentidentität ungeprüft, kein Bewertungsschlüssel."
    ),
    IdentityStatus.NO_CANDIDATE: (
        "Keine vollständige Yahoo-Zuordnung im Blatt; "
        "Instrumentidentität ungeprüft, kein Bewertungsschlüssel."
    ),
}


@dataclass(frozen=True, slots=True)
class WatchlistMembership:
    """One Sheet row with its verified identity, or none.

    ``valuation_key`` is the verified ``instrument_id`` shared with the
    portfolio's identical security; it is ``None`` for every unverified row,
    which nevertheless stays in the result. ``in_portfolio`` is only true for a
    verified row whose security a portfolio position resolves to as well.
    """

    item: WatchlistItem = field(repr=False)
    in_portfolio: bool
    valuation_key: str | None = field(repr=False)
    status: IdentityStatus
    listing: VerifiedListing | None = field(default=None, repr=False)

    @property
    def instrument(self) -> Instrument | None:
        """The registry's verified instrument (never built from Sheet cells)."""
        return None if self.listing is None else self.listing.instrument

    @property
    def explanation(self) -> str:
        return _EXPLANATIONS[self.status]


def _matches(item: WatchlistItem, listing: VerifiedListing) -> bool:
    mapping, instrument = item.mapping, listing.instrument
    unit = instrument.quote_unit
    return (
        instrument.provider_mapping == ProviderMapping(PROVIDER, mapping.symbol)
        and instrument.trading_venue == mapping.exchange
        and unit is not None
        and unit.currency == mapping.currency
        and unit.scale_to_currency == Decimal(1)
        # A Sheet "Typ" that contradicts the verified type never matches.
        and item.instrument_type in (None, instrument.instrument_type)
    )


def _identify(
    item: WatchlistItem, registry: tuple[VerifiedListing, ...]
) -> tuple[IdentityStatus, VerifiedListing | None]:
    if item.status is not WatchlistRowStatus.MAPPED:
        return IdentityStatus.NO_CANDIDATE, None
    for listing in registry:
        if _matches(item, listing):
            if listing.instrument.instrument_type is InstrumentType.STOCK:
                return IdentityStatus.VERIFIED, listing
            return IdentityStatus.VERIFIED_NOT_STOCK, listing
    return IdentityStatus.UNMATCHED, None


def link_portfolio(
    items: tuple[WatchlistItem, ...],
    positions: tuple[Position, ...],
    listings: tuple[VerifiedListing, ...],
) -> tuple[WatchlistMembership, ...]:
    """Give every Sheet row a membership, in row order; none is ever dropped.

    Rule for a "verified" row: the complete Yahoo candidate (symbol, exchange,
    currency) equals the provider symbol, venue and unit of exactly one entry of
    the verified registry ``listings`` and a given ``Typ`` agrees with it. The
    key is that entry's ``instrument_id``, so a Sheet row and a position of the
    same verified security share one key and can be deduplicated before the
    unchanged ``FundamentalRegistry`` lookup; another share class, ADR or
    different security keeps its own key. Everything else stays visible without
    key. No positions, quantities, costs or report sets are changed or valued.
    """
    if not isinstance(items, tuple) or not all(
        isinstance(item, WatchlistItem) for item in items
    ):
        raise TypeError("items must be a tuple of WatchlistItem")
    if not isinstance(positions, tuple) or not all(
        isinstance(p, Position) for p in positions
    ):
        raise TypeError("positions must be a tuple of Position")
    if not isinstance(listings, tuple) or not all(
        isinstance(v, VerifiedListing) for v in listings
    ):
        raise TypeError("listings must be a tuple of VerifiedListing")
    if listings:
        # Validates the whole registry, also for an empty portfolio.
        resolve_listing(listings[0].instrument, listings)
    portfolio_ids = {
        listing.instrument.instrument_id
        for position in positions
        if (listing := resolve_listing(position.instrument, listings)) is not None
    }
    memberships = []
    for item in items:
        status, listing = _identify(item, listings)
        key = None if status is not IdentityStatus.VERIFIED else listing.instrument.instrument_id
        memberships.append(
            WatchlistMembership(
                item,
                listing is not None and listing.instrument.instrument_id in portfolio_ids,
                key,
                status,
                listing,
            )
        )
    return tuple(memberships)
