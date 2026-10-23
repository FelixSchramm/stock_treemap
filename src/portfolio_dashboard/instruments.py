"""Explicit, offline instrument associations supplied after external verification.

There is no symbol search, provider access or built-in portfolio registry here.
Constructing a record asserts that its identity was checked; it is not proof of
that check. Real records belong in protected runtime configuration.
"""

from dataclasses import dataclass
from enum import Enum

from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    Position,
    ProviderMapping,
)


class EquityForm(Enum):
    SHARE = "share"
    ADR = "adr"


@dataclass(frozen=True, slots=True)
class VerifiedListing:
    """One checked security/share class/product on one explicit trading venue.

    instrument_id identifies the security, not its display name, ticker or row.
    Listings of the same security share an ID only after identity verification.
    No provider_mapping means identity is known but automatic coverage is open.
    """

    instrument: Instrument
    equity_form: EquityForm | None = None
    isin: str | None = None
    wkn: str | None = None
    share_class: str | None = None
    product_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, Instrument):
            raise TypeError("instrument must be Instrument")
        if self.instrument.instrument_id is None or self.instrument.trading_venue is None:
            raise ValueError("verified listing requires instrument_id and trading_venue")
        for field in ("isin", "wkn", "share_class", "product_id"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field} must be nonblank text or None")
        kind = self.instrument.instrument_type
        if kind is InstrumentType.STOCK:
            if not isinstance(self.equity_form, EquityForm):
                raise ValueError("stock requires explicit share or ADR equity_form")
        elif self.equity_form is not None:
            raise ValueError("equity_form applies only to stock")
        if kind is InstrumentType.ETF:
            if self.share_class is None or (self.isin is None and self.wkn is None):
                raise ValueError("ETF requires share_class and ISIN or WKN")
        elif self.share_class is not None:
            raise ValueError("share_class applies only to ETF")
        if kind is InstrumentType.DERIVATIVE:
            if self.product_id is None:
                raise ValueError("derivative requires its own product_id")
        elif self.product_id is not None:
            raise ValueError("product_id applies only to derivative")
        unit = self.instrument.quote_unit
        if self.instrument.provider_mapping is not None and (
            unit is None or unit.scale_to_currency is None
        ):
            raise ValueError("provider mapping requires verified currency and unit scale")


def _identity(listing: VerifiedListing) -> tuple:
    return (
        listing.instrument.instrument_type,
        listing.equity_form,
        listing.isin,
        listing.wkn,
        listing.share_class,
        listing.product_id,
    )


def _validate_registry(listings: tuple[VerifiedListing, ...]) -> None:
    if not isinstance(listings, tuple) or not all(
        isinstance(listing, VerifiedListing) for listing in listings
    ):
        raise TypeError("listings must be a tuple of VerifiedListing")
    identities = {}
    listing_keys = set()
    provider_keys = set()
    for listing in listings:
        instrument = listing.instrument
        key = (instrument.instrument_id, instrument.trading_venue)
        if key in listing_keys:
            raise ValueError("duplicate instrument/listing association")
        listing_keys.add(key)
        identity = _identity(listing)
        if instrument.instrument_id in identities:
            if identities[instrument.instrument_id] != identity:
                raise ValueError("conflicting identity metadata for instrument_id")
        identities[instrument.instrument_id] = identity
        mapping = instrument.provider_mapping
        if mapping is not None:
            provider_key = (mapping.provider, mapping.symbol)
            if provider_key in provider_keys:
                raise ValueError("provider symbol assigned to more than one listing")
            provider_keys.add(provider_key)


def _resolve_listing(
    instrument: Instrument, listings: tuple[VerifiedListing, ...]
) -> VerifiedListing | None:
    for listing in listings:
        candidate = listing.instrument
        if (
            instrument.instrument_id != candidate.instrument_id
            or instrument.trading_venue != candidate.trading_venue
            or instrument.instrument_type is not candidate.instrument_type
        ):
            continue
        # Existing contradictory metadata must never be silently overwritten.
        if instrument.quote_unit is not None and (
            instrument.quote_unit != candidate.quote_unit
        ):
            return None
        if instrument.provider_mapping is not None and (
            instrument.provider_mapping != candidate.provider_mapping
        ):
            return None
        return listing
    return None


def resolve_listing(
    instrument: Instrument, listings: tuple[VerifiedListing, ...]
) -> VerifiedListing | None:
    """Exact verified ID/type/venue lookup; names and symbols are never keys.

    Missing identity, missing venue, unknown listing or contradictory metadata
    returns None. A known record may still have no automatic provider coverage.
    """
    if not isinstance(instrument, Instrument):
        raise TypeError("instrument must be Instrument")
    _validate_registry(listings)
    return _resolve_listing(instrument, listings)


def lookup_provider_mapping(
    instrument: Instrument, listings: tuple[VerifiedListing, ...]
) -> ProviderMapping | None:
    """Return only an explicitly verified association, otherwise None."""
    listing = resolve_listing(instrument, listings)
    return None if listing is None else listing.instrument.provider_mapping


def group_positions(
    positions: tuple[Position, ...], listings: tuple[VerifiedListing, ...]
) -> tuple[tuple[Position, ...], ...]:
    """Group verified identical securities, retaining every original member.

    Unresolved positions stay separate, even when their tickers or claimed IDs
    match. No quantities, prices, categories, cost bases or timestamps are merged.
    Group order follows first occurrence; member order follows input order.
    """
    if not isinstance(positions, tuple) or not all(
        isinstance(position, Position) for position in positions
    ):
        raise TypeError("positions must be a tuple of Position")
    _validate_registry(listings)
    groups: list[list[Position]] = []
    verified_groups: dict[str, int] = {}
    for position in positions:
        listing = _resolve_listing(position.instrument, listings)
        if listing is None:
            groups.append([position])
            continue
        instrument_id = listing.instrument.instrument_id
        if instrument_id not in verified_groups:
            verified_groups[instrument_id] = len(groups)
            groups.append([])
        groups[verified_groups[instrument_id]].append(position)
    return tuple(tuple(group) for group in groups)
