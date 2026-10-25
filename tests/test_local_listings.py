"""Private listings file, ISIN assignment and exchange calendars; synthetic only."""

import json
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

import portfolio_dashboard.local_source as local_source
from portfolio_dashboard.config import load_config
from portfolio_dashboard.instruments import EquityForm
from portfolio_dashboard.market_data import (
    AdjustmentBasis,
    PriceHistory,
    PriceObservation,
)
from portfolio_dashboard.models import (
    Instrument,
    InstrumentType,
    PortfolioSnapshot,
    Position,
    Provenance,
    QuoteUnit,
    SnapshotStatus,
    Valuation,
    ValuationSource,
    ValueStatus,
)
from portfolio_dashboard.sheet_parser import SHEET_HEADERS

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)
STOCK = """
[[listing]]
isin = "DE000SYN0001"
type = "stock"
symbol = "SYN.DE"
venue = "GER"
equity_form = "share"
"""
ETF = """
[[listing]]
isin = "LU000SYN0002"
type = "etf"
symbol = "SYNE.DE"
venue = "GER"
share_class = "Acc"
"""


def listings_file(tmp_path, text):
    path = tmp_path / "listings.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def position(isin, kind=InstrumentType.STOCK, currency="EUR"):
    return Position(
        "Synthetische Position",
        Decimal("1"),
        Instrument(kind, source_isin=isin, quote_unit=QuoteUnit(currency, None)),
        None,
        2,
        NOW,
    )


def test_listings_file_builds_verified_eur_listings(tmp_path):
    stock, etf = local_source.load_listings(listings_file(tmp_path, STOCK + ETF))
    assert stock.isin == stock.instrument.instrument_id == "DE000SYN0001"
    assert stock.instrument.trading_venue == "GER"
    assert stock.instrument.provider_mapping.symbol == "SYN.DE"
    assert stock.instrument.provider_mapping.provider == "yfinance"
    assert stock.instrument.quote_unit == QuoteUnit("EUR", Decimal("1"))
    assert stock.equity_form is EquityForm.SHARE
    assert etf.instrument.instrument_type is InstrumentType.ETF
    assert etf.share_class == "Acc"


@pytest.mark.parametrize(
    "broken",
    [
        STOCK.replace('symbol = "SYN.DE"\n', ""),
        STOCK.replace('"GER"', '"XNYS"'),
        STOCK.replace('"stock"', '"derivative"'),
        STOCK.replace('equity_form = "share"\n', ""),
        ETF.replace('share_class = "Acc"\n', ""),
        STOCK.replace('"DE000SYN0001"', "1"),
    ],
)
def test_incomplete_or_unsupported_entries_are_rejected_without_values(
    tmp_path, broken
):
    with pytest.raises(local_source.ListingsFileError, match="Eintrag 1") as error:
        local_source.load_listings(listings_file(tmp_path, broken))
    assert "SYN" not in str(error.value)


@pytest.mark.parametrize(
    "duplicate",
    [
        STOCK + STOCK.replace("SYN.DE", "SYN2.DE"),
        STOCK + STOCK.replace("DE000SYN0001", "DE000SYN0003"),
    ],
)
def test_isin_and_symbol_must_be_unique(tmp_path, duplicate):
    with pytest.raises(local_source.ListingsFileError, match="mehrfach"):
        local_source.load_listings(listings_file(tmp_path, duplicate))


def test_missing_or_malformed_file_names_no_path(tmp_path):
    for path in (str(tmp_path / "missing.toml"), listings_file(tmp_path, "[[listing")):
        with pytest.raises(local_source.ListingsFileError) as error:
            local_source.load_listings(path)
        assert str(tmp_path) not in str(error.value)


def test_positions_get_the_verified_identity_only_for_matching_isin_and_type(tmp_path):
    listings = local_source.load_listings(listings_file(tmp_path, STOCK))
    snapshot = PortfolioSnapshot(
        NOW,
        SnapshotStatus.COMPLETE,
        (
            position("DE000SYN0001"),
            # Column F names the Sheet's home listing; the owner chose EUR instead.
            position("DE000SYN0001", currency="USD"),
            position("DE000SYN0001", kind=InstrumentType.ETF),
            position("DE000SYN0009"),
        ),
    )
    eur, home_usd, wrong_type, unknown = local_source.assign_listings(
        snapshot, listings
    ).positions
    for matched in (eur, home_usd):
        assert matched.instrument.instrument_id == "DE000SYN0001"
        assert matched.instrument.trading_venue == "GER"
        assert matched.instrument.quote_unit == QuoteUnit("EUR", Decimal("1"))
    for unchanged in (wrong_type, unknown):
        assert unchanged.instrument.instrument_id is None
        assert unchanged.instrument.trading_venue is None


def test_calendars_skip_weekends_and_german_exchange_holidays():
    calendars = local_source.venue_calendars(date(2026, 10, 25))
    xetra, frankfurt = calendars["GER"], calendars["FRA"]
    assert (xetra.open_time, xetra.close_time) == (time(9), time(17, 30))
    assert (frankfurt.open_time, frankfurt.close_time) == (time(8), time(22))
    assert xetra.first_day <= date(2025, 10, 24) and xetra.last_day >= date(
        2026, 10, 25
    )
    closed = [
        date(2025, 12, 24),
        date(2025, 12, 25),
        date(2025, 12, 26),
        date(2025, 12, 31),
        date(2026, 1, 1),
        date(2026, 4, 3),
        date(2026, 4, 6),
        date(2026, 5, 1),
        date(2026, 10, 24),
        date(2026, 10, 25),
    ]
    assert not set(closed) & xetra.sessions
    # Whit Monday and Ascension Day are regular sessions on German exchanges.
    assert {date(2026, 5, 14), date(2026, 5, 25), date(2026, 10, 23)} <= xetra.sessions


class SyntheticResponse:
    status_code = 200

    def __init__(self, rows):
        self.body = json.dumps({"majorDimension": "ROWS", "values": rows})

    def json(self, **kwargs):
        return json.loads(self.body, **kwargs)

    def close(self):
        pass


class SyntheticSession:
    def get(self, url, **kwargs):
        header = [*SHEET_HEADERS, "ISIN"]
        row = [
            "Synthetische Aktie",
            "SYN",
            "Aktie",
            "2",
            "SYNX:SYN",
            "EUR",
            "50,00",
            "100,00",
            "80,00",
            "25",
            "20,00",
            "Synthetisch",
            "100",
            "DE000SYN0001",
        ]
        return SyntheticResponse([header, row])


class EurProvider:
    """Answers only the verified listing; history stays unavailable."""

    def __init__(self, listings, *, listing_resolver=None):
        self.symbols = {
            listing.instrument.provider_mapping.symbol for listing in listings
        }

    def latest_quote(self, instrument):
        assert instrument.provider_mapping.symbol in self.symbols
        provenance = Provenance(ValuationSource.PROVIDER, NOW, NOW, "yfinance")
        return PriceObservation(
            Valuation(
                Decimal("55"), ValueStatus.AVAILABLE, provenance, instrument.quote_unit
            ),
            AdjustmentBasis.YAHOO_SPLIT_ADJUSTED_PRICE,
        )

    def history(self, instrument, start, end, *, minimum_points=2):
        provenance = Provenance(ValuationSource.PROVIDER, NOW, provider="yfinance")
        return PriceHistory(ValueStatus.MISSING, provenance)

    def fx(self, pair):
        raise AssertionError("EUR listings need no FX")


def test_local_coordinator_values_assigned_listings_with_provider_quotes(
    tmp_path, monkeypatch
):
    settings = {
        "PORTFOLIO_MODE": "local",
        "PORTFOLIO_SHEET_ID": "synthetic-source-id",
        "PORTFOLIO_SHEET_RANGE": "'Synthetic tab'!A1:N10",
        "PORTFOLIO_SHEETS_CREDENTIALS_FILE": "/nonexistent/synthetic-credential.json",
        "PORTFOLIO_LISTINGS_FILE": listings_file(tmp_path, STOCK),
    }
    monkeypatch.setattr(
        local_source, "build_readonly_session", lambda path: SyntheticSession()
    )
    monkeypatch.setattr(local_source, "YFinanceProvider", EurProvider)
    bundle = local_source.build_local_coordinator(load_config(settings)).load()

    assert bundle.available
    (member,) = bundle.valuation.positions
    assert member.position.valuation.provenance.source is ValuationSource.PROVIDER
    assert bundle.valuation.total_eur == Decimal("110")
    assert bundle.returns.total.return_percent == Decimal("37.5")
    assert bundle.period_reasons["Heute"] == ("history_unavailable",)


def test_secret_entries_use_the_same_listing_rules_as_the_file(tmp_path):
    entries = [
        {"isin": "DE000SYN0001", "type": "stock", "symbol": "SYN.DE",
         "venue": "GER", "equity_form": "share"},
        {"isin": "LU000SYN0002", "type": "etf", "symbol": "SYNE.DE",
         "venue": "GER", "share_class": "Acc"},
    ]
    assert local_source.parse_listings(entries) == local_source.load_listings(
        listings_file(tmp_path, STOCK + ETF)
    )
    assert local_source.parse_listings([]) == ()
    with pytest.raises(local_source.ListingsFileError, match=r"\[\[listing\]\]"):
        local_source.parse_listings({"isin": "DE000SYN0001"})
    with pytest.raises(local_source.ListingsFileError, match="Eintrag 1") as error:
        local_source.parse_listings([entries[0] | {"venue": "XNYS"}])
    assert "SYN" not in str(error.value)


def test_production_coordinator_uses_secret_key_and_listings(monkeypatch):
    settings = {
        "PORTFOLIO_MODE": "production",
        "PORTFOLIO_OWNER_ISSUER": "https://accounts.google.com",
        "PORTFOLIO_OWNER_SUBJECT": "synthetic-owner-subject",
        "PORTFOLIO_SHEET_ID": "synthetic-source-id",
        "PORTFOLIO_SHEET_RANGE": "'Synthetic tab'!A1:N10",
    }
    auth = {
        "client_id": "synthetic-client-id",
        "client_secret": "synthetic-client-secret",
        "cookie_secret": "synthetic-cookie-secret",
        "redirect_uri": "https://dashboard.example.invalid/oauth2callback",
        "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
    }
    key = {"type": "service_account", "synthetic": True}
    seen = []

    def session_from_info(info):
        seen.append(info)
        return SyntheticSession()

    def no_file(path):
        raise AssertionError("Production read a key file")

    monkeypatch.setattr(local_source, "build_readonly_session", no_file)
    monkeypatch.setattr(
        local_source, "build_readonly_session_from_info", session_from_info
    )
    monkeypatch.setattr(local_source, "YFinanceProvider", EurProvider)
    entries = [{"isin": "DE000SYN0001", "type": "stock", "symbol": "SYN.DE",
                "venue": "GER", "equity_form": "share"}]
    bundle = local_source.build_production_coordinator(
        load_config(settings, auth_settings=auth), key, entries
    ).load()

    assert seen == [key]
    assert bundle.available
    (member,) = bundle.valuation.positions
    assert member.position.valuation.provenance.source is ValuationSource.PROVIDER
    assert bundle.valuation.total_eur == Decimal("110")
