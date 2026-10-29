"""Private runtime mapping of verified fundamentals identities (M8-05, #74).

The repository contains no mapping. Local mode reads a TOML file named by
``PORTFOLIO_FUNDAMENTALS_FILE``; production reads ``[[fundamental]]`` from the
host secrets, the same pattern as the listings. Each entry is the owner's
statement that a listing, issuer, share class and Yahoo symbol belong together,
which currency the company reports in, its fiscal year end, how many ordinary
share classes the issuer has and which accounting assumptions are confirmed.
Parsing never searches, infers or repairs a value, and error messages name only
the entry number, never file content, paths or values.
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from portfolio_dashboard.fundamentals import (
    CapitalStructure,
    FundamentalRegistry,
    FundamentalSource,
    IssuerKind,
    VerifiedFundamentalIdentity,
)
from portfolio_dashboard.fundamentals_normalization import (
    PROVIDER,
    OwnerConfirmations,
    ProviderExpectation,
)
from portfolio_dashboard.instruments import EquityForm, VerifiedListing, resolve_listing
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    ProviderMapping,
    QuoteUnit,
)

_REQUIRED = frozenset(
    {
        "isin",
        "venue",
        "symbol",
        "trading_currency",
        "equity_form",
        "issuer_id",
        "share_class_id",
        "consolidation_scope",
        "issuer_kind",
        "evidence_reference",
        "verified_at",
        "reporting_currency",
        "fiscal_year_end",
    }
)
_OPTIONAL = frozenset(
    {
        "source_symbol",
        "quote_unit_scale",
        "ordinary_share_classes",
        "class_count_evidence",
        "class_count_verified_at",
        "confirm_ebit_definition",
        "confirm_share_split_basis",
        "confirm_lease_treatment",
    }
)


class FundamentalsConfigError(ValueError):
    """Safe message naming an entry number, never file content or values."""


@dataclass(frozen=True, slots=True, repr=False)
class FundamentalSubject:
    """One verified identity with the owner's expectations and confirmations.

    ``class_count`` is the owner's proof of the number of ordinary share classes
    including unlisted ones; the provider never supplies it. ``None`` stays an
    unknown count and blocks a per-share value.
    """

    identity: VerifiedFundamentalIdentity
    expectation: ProviderExpectation
    class_count: int | None = None
    class_count_evidence: str | None = None
    class_count_verified_at: datetime | None = None
    confirmations: OwnerConfirmations = field(default_factory=OwnerConfirmations)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, VerifiedFundamentalIdentity):
            raise TypeError("identity must be VerifiedFundamentalIdentity")
        if not isinstance(self.expectation, ProviderExpectation):
            raise TypeError("expectation must be ProviderExpectation")
        if not isinstance(self.confirmations, OwnerConfirmations):
            raise TypeError("confirmations must be OwnerConfirmations")
        # Validates the proof (count needs evidence, time and unlisted classes).
        self.capital_structure(date(2000, 1, 1))

    @property
    def fetch_key(self) -> tuple:
        """Deduplication identity: issuer, share class, security, source, scope."""
        return self.identity.data_key

    def capital_structure(self, as_of: date) -> CapitalStructure:
        """The owner's class-count proof, dated at the share count it is paired with."""
        return CapitalStructure(
            self.identity.issuer_id,
            self.identity.source,
            as_of,
            self.class_count,
            self.class_count is not None,
            self.class_count_evidence,
            self.class_count_verified_at,
        )

    def owner_digest(self) -> str:
        """Short stable text over owner inputs that change a report set's meaning."""
        c = self.confirmations
        flags = "".join(
            "1" if value else "0"
            for value in (c.ebit_definition, c.share_split_basis, c.lease_treatment)
        )
        count = "u" if self.class_count is None else str(self.class_count)
        return f"c{count}f{flags}"

    def _same_owner_inputs(self, other: "FundamentalSubject") -> bool:
        return (
            self.expectation,
            self.class_count,
            self.class_count_evidence,
            self.class_count_verified_at,
            self.confirmations,
            self.identity.verified_at,
            self.identity.evidence_reference,
        ) == (
            other.expectation,
            other.class_count,
            other.class_count_evidence,
            other.class_count_verified_at,
            other.confirmations,
            other.identity.verified_at,
            other.identity.evidence_reference,
        )


@dataclass(frozen=True, slots=True, repr=False)
class FundamentalSubjects:
    """Validated subjects; listings of one security share exactly one fetch key."""

    subjects: tuple[FundamentalSubject, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.subjects, tuple) or not all(
            isinstance(s, FundamentalSubject) for s in self.subjects
        ):
            raise TypeError("subjects must be a tuple of FundamentalSubject")
        try:
            # Reuses the registry's conflict checks (ids, classes, issuers, venues).
            FundamentalRegistry(tuple(s.identity for s in self.subjects), ())
        except ValueError:
            raise FundamentalsConfigError(
                "Fundamentaldaten-Zuordnung: widersprüchliche Einträge."
            ) from None
        first: dict[tuple, FundamentalSubject] = {}
        for subject in self.subjects:
            prior = first.setdefault(subject.fetch_key, subject)
            if not prior._same_owner_inputs(subject):
                raise FundamentalsConfigError(
                    "Fundamentaldaten-Zuordnung: Einträge derselben Aktie "
                    "widersprechen sich in Währung, Klassenanzahl oder Bestätigungen."
                )

    def resolve(self, instrument: Instrument) -> FundamentalSubject | None:
        """Exact verified id/type/venue lookup; names and tickers are never keys."""
        listing = resolve_listing(
            instrument, tuple(s.identity.listing for s in self.subjects)
        )
        if listing is None:
            return None
        return next(s for s in self.subjects if s.identity.listing is listing)

    def __len__(self) -> int:
        return len(self.subjects)


def load_fundamental_subjects(path: str) -> FundamentalSubjects:
    """Read the ``[[fundamental]]`` entries of a private TOML file."""
    try:
        with open(path, "rb") as file:
            entries = tomllib.load(file).get("fundamental", [])
    except (OSError, tomllib.TOMLDecodeError):
        raise FundamentalsConfigError(
            "Fundamentaldaten-Zuordnungsdatei fehlt oder ist ungültig."
        ) from None
    return parse_fundamental_subjects(entries)


def _text(entry: Mapping, name: str) -> str:
    value = entry[name]
    if not isinstance(value, str) or not value.strip():
        raise ValueError
    return value.strip()


def _moment(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError
    return value


def _flag(entry: Mapping, name: str) -> bool:
    value = entry.get(name, False)
    if type(value) is not bool:
        raise ValueError
    return value


def _parse_entry(entry: object) -> FundamentalSubject:
    if not isinstance(entry, Mapping):
        raise ValueError
    keys = set(entry)
    if not _REQUIRED <= keys or keys - _REQUIRED - _OPTIONAL:
        raise ValueError  # Missing or unknown (typo) key: never guess a default.
    month, _, day = _text(entry, "fiscal_year_end").partition("-")
    scale = entry.get("quote_unit_scale", 1)
    if isinstance(scale, bool) or not isinstance(scale, (int, str)):
        raise ValueError
    symbol, isin = _text(entry, "symbol"), _text(entry, "isin")
    listing = VerifiedListing(
        Instrument(
            InstrumentType.STOCK,
            instrument_id=isin,
            trading_venue=_text(entry, "venue"),
            quote_unit=QuoteUnit(_text(entry, "trading_currency"), Decimal(str(scale))),
            provider_mapping=ProviderMapping(PROVIDER, symbol),
        ),
        equity_form=EquityForm(_text(entry, "equity_form")),
        isin=isin,
    )
    identity = VerifiedFundamentalIdentity(
        listing,
        _text(entry, "share_class_id"),
        _text(entry, "issuer_id"),
        # Several listings of one security share one fetch only with one source
        # symbol; without ``source_symbol`` the listing's own symbol is the source.
        FundamentalSource(
            PROVIDER,
            _text(entry, "source_symbol") if "source_symbol" in entry else symbol,
        ),
        _text(entry, "consolidation_scope"),
        IssuerKind(_text(entry, "issuer_kind")),
        _text(entry, "evidence_reference"),
        _moment(entry["verified_at"]),
    )
    count = entry.get("ordinary_share_classes")
    if count is not None and (type(count) is not int or count < 1):
        raise ValueError
    evidence = entry.get("class_count_evidence")
    verified = entry.get("class_count_verified_at")
    return FundamentalSubject(
        identity,
        ProviderExpectation(_text(entry, "reporting_currency"), int(month), int(day)),
        count,
        None if evidence is None else _text(entry, "class_count_evidence"),
        None if verified is None else _moment(verified),
        OwnerConfirmations(
            _flag(entry, "confirm_ebit_definition"),
            _flag(entry, "confirm_share_split_basis"),
            _flag(entry, "confirm_lease_treatment"),
        ),
    )


def parse_fundamental_subjects(entries: object) -> FundamentalSubjects:
    """Validate entries; any doubt raises, nothing is defaulted or repaired."""
    if not isinstance(entries, (list, tuple)):
        raise FundamentalsConfigError(
            "Fundamentaldaten-Zuordnung enthält keine Liste [[fundamental]]."
        )
    subjects = []
    for number, entry in enumerate(entries, 1):
        try:
            subjects.append(_parse_entry(entry))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise FundamentalsConfigError(
                f"Fundamentaldaten-Zuordnung: Eintrag {number} ist unvollständig "
                "oder ungültig."
            ) from None
    return FundamentalSubjects(tuple(subjects))
