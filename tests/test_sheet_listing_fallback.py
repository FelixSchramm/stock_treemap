"""Synthetic Sheet -> loader -> real adapter -> cache -> UI-model regression tests."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

import portfolio_dashboard.local_source as local_source
from portfolio_dashboard.config import load_config
from portfolio_dashboard.market_data import YFinanceProvider
from portfolio_dashboard.models import ValuationSource
from portfolio_dashboard.sheet_parser import SHEET_HEADERS
from portfolio_dashboard.treemap import (
    build_position_details,
    build_status_notes,
    build_table_rows,
)

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)
ISIN = "DE000SYN0001"
PRIMARY = {"isin": ISIN, "type": "stock", "symbol": "PRIMARY.DE",
           "venue": "GER", "equity_form": "share"}
HEADERS = [*SHEET_HEADERS, "", "", "", "", "ISIN", "Handelsplatz", "Kursdatum",
           "Yahoo-Symbol", "Yahoo-Handelsplatz", "Yahoo-Aktienform", "Yahoo-Anteilsklasse"]


def sheet_row(symbol="ALTERNATIVE.DE", *, venue="GER", form="share", share_class="",
              isin=ISIN, kind="Aktie", name="Synthetische Position"):
    return [name, "SYNTH", kind, 2, "IGNORED:SYNTH", "USD", 50, 100, 80,
            25, 20, "Synthetisch", 100, "", "", "", "", isin, "", "",
            symbol, venue, form, share_class]


class SyntheticResponse:
    status_code = 200

    def __init__(self, rows):
        self.rows = rows

    def json(self, **kwargs):
        return {"majorDimension": "ROWS", "values": self.rows}

    def close(self):
        pass


class MutableSession:
    def __init__(self, rows):
        self.rows = rows
        self.failed = False
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.failed:
            raise OSError("SYNTHETIC PRIVATE TRANSPORT DETAIL")
        return SyntheticResponse(self.rows)


class SyntheticMarket:
    """Only Ticker's transport seam is synthetic; all adapter checks remain real."""

    def __init__(self, cases):
        self.cases = cases
        self.calls = []

    def ticker(self, symbol):
        case = self.cases.get(symbol, {})
        market = self

        class Ticker:
            def history(self, **kwargs):
                is_quote = "period" in kwargs
                market.calls.append((symbol, "quote" if is_quote else "history"))
                if case.get("quote_error") and is_quote or case.get("history_error") and not is_quote:
                    raise OSError("SYNTHETIC PRIVATE PROVIDER DETAIL")
                days = sorted(day for day in local_source.venue_calendars(NOW.date())["GER"].sessions
                              if day <= NOW.date())
                if not is_quote:
                    days = [day for day in days if kwargs["start"] <= day.isoformat() < kwargs["end"]]
                    if "first_day" in case:
                        days = [day for day in days if day >= case["first_day"]]
                    days = [day for day in days if day not in case.get("omit", ())]
                    if case.get("one_point"):
                        days = days[-1:]
                closes = [case.get("close", 100.0)] * len(days)
                if case.get("invalid_price") and not is_quote:
                    closes[0] = float("nan")
                return pd.DataFrame(
                    {"Close": closes, "Stock Splits": [0.0] * len(days)},
                    index=pd.DatetimeIndex(days, tz="Europe/Berlin"),
                )

            def get_history_metadata(self):
                return {
                    "symbol": symbol,
                    "exchangeName": case.get("metadata_venue", "GER"),
                    "currency": case.get("currency", "EUR"),
                    "exchangeTimezoneName": "Europe/Berlin",
                    "regularMarketPrice": case.get("price", 110.0),
                    "regularMarketTime": pd.Timestamp(NOW),
                }

        return Ticker()


def loader(monkeypatch, tmp_path, *, primary=True, rows=None, cases=None, mode="local"):
    if isinstance(primary, list):
        entries = primary
    else:
        entries = [primary if isinstance(primary, dict) else PRIMARY] if primary else []
    session = MutableSession([HEADERS, sheet_row()] if rows is None else rows)
    market = SyntheticMarket(cases or {})
    monkeypatch.setattr(local_source, "build_readonly_session", lambda path: session)
    monkeypatch.setattr(local_source, "build_readonly_session_from_info", lambda info: session)
    monkeypatch.setattr(local_source, "YFinanceProvider", lambda listings, **kwargs: YFinanceProvider(
        listings, ticker_factory=market.ticker, clock=lambda: NOW, **kwargs
    ))
    settings = {
        "PORTFOLIO_MODE": mode,
        "PORTFOLIO_SHEET_ID": "synthetic-source-id",
        "PORTFOLIO_SHEET_RANGE": "'Synthetic tab'!A1:X20",
    }
    if mode == "local":
        settings["PORTFOLIO_SHEETS_CREDENTIALS_FILE"] = "/nonexistent/synthetic.json"
        if primary:
            path = tmp_path / "synthetic-listings.toml"
            path.write_text("[[listing]]\n" + "\n".join(
                f"{key}={json.dumps(value)}" for key, value in entries[0].items()
            ))
            settings["PORTFOLIO_LISTINGS_FILE"] = str(path)
        coordinator = local_source.build_local_coordinator(load_config(settings))
    else:
        settings |= {"PORTFOLIO_OWNER_ISSUER": "https://accounts.google.com",
                     "PORTFOLIO_OWNER_SUBJECT": "synthetic-subject"}
        auth = {"client_id": "synthetic-client", "client_secret": "synthetic-secret",
                "cookie_secret": "synthetic-cookie",
                "redirect_uri": "https://example.invalid/oauth2callback",
                "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration"}
        coordinator = local_source.build_production_coordinator(
            load_config(settings, auth_settings=auth), {"synthetic": True}, entries
        )
    coordinator._clock = lambda: NOW
    coordinator._cache.clock = lambda: NOW
    return coordinator, session, market


def active_symbol(bundle):
    assert bundle.available
    return bundle.snapshot.positions[0].instrument.provider_mapping.symbol


def status_surfaces(bundle, period="Heute"):
    perfs = bundle.performances_for("Kursentwicklung", period)
    reasons = bundle.reasons_for("Kursentwicklung", period)
    rows = build_table_rows(bundle.valuation, perfs, period_reasons=reasons)
    return rows, build_position_details(rows), build_status_notes(bundle.valuation, perfs, period_reasons=reasons)


@pytest.mark.parametrize("mode", ["local", "production"])
def test_primary_wins_full_success_and_old_sheets_remain_compatible(monkeypatch, tmp_path, mode):
    coordinator, session, market = loader(monkeypatch, tmp_path, mode=mode)
    bundle = coordinator.load()
    assert active_symbol(bundle) == "PRIMARY.DE"
    assert all(bundle.course_performances[p][0].percent == Decimal(10) for p in bundle.course_performances)
    assert not any(symbol == "ALTERNATIVE.DE" for symbol, kind in market.calls)
    assert "Kurszuordnung: App" in status_surfaces(bundle)[0][0].status
    # A–T sheets without the optional fields continue to use the primary mapping.
    session.rows = [HEADERS[:20], sheet_row()[:20]]
    assert active_symbol(coordinator.load(refresh=True)) == "PRIMARY.DE"


@pytest.mark.parametrize("mode", ["local", "production"])
def test_complete_sheet_mapping_supplies_missing_primary_in_both_loaders(monkeypatch, tmp_path, mode):
    coordinator, session, market = loader(monkeypatch, tmp_path, primary=False, mode=mode)
    bundle = coordinator.load()
    assert active_symbol(bundle) == "ALTERNATIVE.DE"
    assert bundle.valuation.total_eur == Decimal(220)
    assert all(bundle.course_performances[p][0].percent == Decimal(10) for p in bundle.course_performances)
    rows, details, notes = status_surfaces(bundle)
    assert rows[0].quote_mapping == "Sheet-Ersatz · yfinance · ALTERNATIVE.DE · GER"
    assert "Sheet-Ersatz" in " ".join(details[0])
    assert "Sheet-Ersatz" in " ".join(notes)
    personal_rows = build_table_rows(bundle.valuation, bundle.performances_for("Meine Entwicklung", "Seit Kauf"),
                                    is_personal_view=True, returns=bundle.returns)
    assert personal_rows[0].quote_mapping == rows[0].quote_mapping
    assert "Sheet-Ersatz" in personal_rows[0].status
    assert "A1%3AX20" in session.calls[0][0]


@pytest.mark.parametrize("failure", [{"quote_error": True}, {"invalid_price": True}])
def test_quote_or_history_failure_uses_same_venue_replacement_without_mixed_returns(monkeypatch, tmp_path, failure):
    coordinator, _, market = loader(monkeypatch, tmp_path, cases={
        "PRIMARY.DE": failure | {"price": 150.0, "close": 50.0},
        "ALTERNATIVE.DE": {"price": 220.0, "close": 200.0},
    })
    bundle = coordinator.load()
    assert active_symbol(bundle) == "ALTERNATIVE.DE"
    assert bundle.valuation.total_eur == Decimal(440)
    assert all(bundle.course_performances[p][0].percent == Decimal(10) for p in bundle.course_performances)
    assert ("ALTERNATIVE.DE", "quote") in market.calls
    assert ("ALTERNATIVE.DE", "history") in market.calls


def test_alternative_never_discards_an_available_primary_period(monkeypatch, tmp_path):
    coordinator, _, market = loader(monkeypatch, tmp_path, cases={
        "PRIMARY.DE": {"first_day": date(2026, 9, 1), "omit": {
            date(2026, 10, 16), date(2026, 10, 15), date(2026, 10, 14), date(2026, 10, 13)}},
        "ALTERNATIVE.DE": {"omit": {date(2026, 10, 22)}},
    })
    bundle = coordinator.load()
    assert active_symbol(bundle) == "PRIMARY.DE"
    assert {p for p, perfs in bundle.course_performances.items() if perfs[0].percent is not None} == {"Heute", "Monat"}
    assert ("ALTERNATIVE.DE", "history") in market.calls
    assert "ohne zusätzliche nutzbare Perioden" in status_surfaces(bundle)[0][0].status


def test_true_period_superset_can_replace_partial_primary_but_ties_keep_primary(monkeypatch, tmp_path):
    coordinator, _, _ = loader(monkeypatch, tmp_path, cases={
        "PRIMARY.DE": {"first_day": date(2026, 9, 1)},
    })
    assert active_symbol(coordinator.load()) == "ALTERNATIVE.DE"
    coordinator, _, _ = loader(monkeypatch, tmp_path, cases={
        "PRIMARY.DE": {"first_day": date(2026, 9, 1)},
        "ALTERNATIVE.DE": {"first_day": date(2026, 9, 1)},
    })
    assert active_symbol(coordinator.load()) == "PRIMARY.DE"


@pytest.mark.parametrize(("case", "cause"), [
    ({"quote_error": True}, "Kursanbieter nicht erreichbar"),
    ({"currency": "USD"}, "Währung oder Kurseinheit unklar"),
    ({"metadata_venue": "FRA"}, "Instrument oder Handelsplatz weicht ab"),
])
def test_failed_alternative_remains_flagged_sheet_value_and_safe_reason(monkeypatch, tmp_path, case, cause):
    coordinator, _, _ = loader(monkeypatch, tmp_path, primary=False, cases={"ALTERNATIVE.DE": case})
    bundle = coordinator.load()
    assert bundle.valuation.total_eur == Decimal(100)
    assert bundle.snapshot.positions[0].valuation.provenance.source is ValuationSource.SHEET
    rows, details, notes = status_surfaces(bundle)
    for text in (rows[0].status, " ".join(details[0]), " ".join(notes)):
        assert f"Kursabruf fehlgeschlagen ({cause})" in text
        assert "SYNTHETIC PRIVATE" not in text
    assert all(perfs[0].percent is None for perfs in bundle.course_performances.values())


def test_double_history_failure_keeps_primary_quote_and_concrete_cause(monkeypatch, tmp_path):
    coordinator, _, _ = loader(monkeypatch, tmp_path, cases={
        "PRIMARY.DE": {"invalid_price": True}, "ALTERNATIVE.DE": {"one_point": True},
    })
    bundle = coordinator.load()
    assert active_symbol(bundle) == "PRIMARY.DE"
    assert bundle.valuation.total_eur == Decimal(220)
    status = status_surfaces(bundle)[0][0].status
    assert "Kurshistorie nicht verfügbar (Kurs ungültig)" in status
    assert "Sheet-Ersatzzuordnung ohne zusätzliche nutzbare Perioden" in status
    assert "Zu wenige Tageskurse" in status
    assert all(perfs[0].percent is None for perfs in bundle.course_performances.values())


def test_both_quote_failures_keep_primary_reason_and_alternative_evidence(monkeypatch, tmp_path):
    coordinator, _, _ = loader(monkeypatch, tmp_path, cases={
        "PRIMARY.DE": {"quote_error": True}, "ALTERNATIVE.DE": {"currency": "USD"},
    })
    bundle = coordinator.load()
    assert bundle.available and bundle.valuation.total_eur == Decimal(100)
    status = status_surfaces(bundle)[0][0].status
    assert "Kursabruf fehlgeschlagen (Kursanbieter nicht erreichbar)" in status
    assert "Währung oder Kurseinheit unklar" in status
    assert "sheet_alternative_unknown_or_conflicting_unit" in {
        w.code for w in bundle.snapshot.positions[0].warnings
    }


@pytest.mark.parametrize("row", [
    sheet_row(form="adr"), sheet_row(venue="XNYS"), sheet_row(form=""),
    sheet_row(symbol="https://example.invalid/private"),
    sheet_row(kind="Derivat"), sheet_row(isin="not-an-isin"),
])
def test_bad_optional_mapping_is_isolated_from_other_holdings(monkeypatch, tmp_path, row):
    good = sheet_row(isin="DE000SYN0002", symbol="OTHER.DE", name="Andere synthetische Position")
    coordinator, _, market = loader(monkeypatch, tmp_path, rows=[HEADERS, row, good])
    bundle = coordinator.load()
    assert bundle.available
    assert len(bundle.valuation.groups) == 2
    assert bundle.snapshot.positions[1].instrument.provider_mapping.symbol == "OTHER.DE"
    assert any(w.code == "invalid_yahoo_listing" and w.source_row == 2 for w in bundle.snapshot.warnings)
    assert not any(symbol == "ALTERNATIVE.DE" for symbol, kind in market.calls)


def test_refresh_replaces_and_removes_mapping_and_failed_holdings_preserve_bundle(monkeypatch, tmp_path):
    coordinator, session, market = loader(monkeypatch, tmp_path, primary=False)
    first = coordinator.load()
    assert active_symbol(first) == "ALTERNATIVE.DE"
    session.rows = [HEADERS, sheet_row(symbol="CHANGED.DE")]
    changed = coordinator.load(refresh=True)
    assert active_symbol(changed) == "CHANGED.DE"
    assert ("CHANGED.DE", "quote") in market.calls
    calls = list(market.calls)
    session.failed = True
    retained = coordinator.load(refresh=True)
    assert retained.using_cached_stand
    assert retained.snapshot == changed.snapshot
    assert retained.course_performances == changed.course_performances
    assert market.calls == calls
    session.failed = False
    coordinator._clock = lambda: NOW + timedelta(minutes=1)
    coordinator._cache.clock = coordinator._clock  # Respect the existing holdings retry backoff.
    session.rows = [HEADERS, sheet_row(symbol="", venue="", form="")]
    removed = coordinator.load(refresh=True)
    assert removed.available
    assert removed.snapshot.positions[0].instrument.provider_mapping is None
    assert removed.valuation.total_eur == Decimal(100)
    assert "Keine Kurszuordnung" in status_surfaces(removed)[0][0].status
    assert market.calls == calls


def test_repeated_rows_share_one_selected_product_mapping(monkeypatch, tmp_path):
    # A mapping for the same exact identity also covers another partial holding.
    coordinator, _, _ = loader(monkeypatch, tmp_path, primary=False, rows=[
        HEADERS, sheet_row(), sheet_row(symbol="", venue="", form=""),
    ])
    bundle = coordinator.load()
    assert len(bundle.valuation.groups) == 1
    assert bundle.valuation.total_eur == Decimal(440)


def test_header_binding_accepts_reordered_columns_and_rejects_duplicate_product_symbols(monkeypatch, tmp_path):
    headers = list(HEADERS)
    row = sheet_row()
    headers[20], headers[21] = headers[21], headers[20]
    row[20], row[21] = row[21], row[20]
    coordinator, _, _ = loader(monkeypatch, tmp_path, primary=False, rows=[headers, row])
    assert active_symbol(coordinator.load()) == "ALTERNATIVE.DE"
    coordinator, _, market = loader(monkeypatch, tmp_path, primary=False, rows=[
        HEADERS, sheet_row(), sheet_row(isin="DE000SYN0002"),
    ])
    bundle = coordinator.load()
    assert bundle.available and bundle.valuation.total_eur == Decimal(200)
    assert not market.calls
    assert sum(w.code == "invalid_yahoo_listing" for w in bundle.snapshot.warnings) == 2


def test_etf_sheet_mapping_requires_matching_explicit_share_class(monkeypatch, tmp_path):
    row = sheet_row(kind="ETF", form="", share_class="Acc")
    coordinator, _, _ = loader(monkeypatch, tmp_path, primary=False, rows=[HEADERS, row], mode="production")
    bundle = coordinator.load()
    assert active_symbol(bundle) == "ALTERNATIVE.DE"
    assert all(perfs[0].percent == Decimal(10) for perfs in bundle.course_performances.values())
    primary = {"isin": ISIN, "type": "etf", "symbol": "PRIMARY.DE", "venue": "GER", "share_class": "Dist"}
    coordinator, _, market = loader(monkeypatch, tmp_path, primary=primary, rows=[HEADERS, row], mode="production")
    bundle = coordinator.load()
    assert active_symbol(bundle) == "PRIMARY.DE"
    assert any(w.code == "invalid_yahoo_listing" for w in bundle.snapshot.warnings)
    assert not any(symbol == "ALTERNATIVE.DE" for symbol, kind in market.calls)
    # The same ISIN must not become another instrument type through an optional mapping.
    coordinator, _, market = loader(monkeypatch, tmp_path, rows=[HEADERS, row])
    bundle = coordinator.load()
    assert bundle.available and not market.calls
    assert any(w.code == "invalid_yahoo_listing" for w in bundle.snapshot.warnings)


def test_duplicate_yahoo_header_is_a_safe_optional_error(monkeypatch, tmp_path):
    headers = list(HEADERS)
    headers[23] = "Yahoo-Symbol"
    coordinator, _, market = loader(monkeypatch, tmp_path, primary=False, rows=[headers, sheet_row()])
    bundle = coordinator.load()
    assert bundle.available and bundle.valuation.total_eur == Decimal(100)
    assert not market.calls
    assert any(w.code == "duplicate_yahoo_column" for w in bundle.snapshot.warnings)


def three_listing_setup(monkeypatch, tmp_path, cases):
    """Three synthetic holdings, each with an app listing and a Sheet replacement."""
    primaries, rows = [], [HEADERS]
    for index in range(1, 4):
        isin = f"DE000SYN000{index}"
        primaries.append(PRIMARY | {"isin": isin, "symbol": f"PRIMARY{index}.DE"})
        rows.append(sheet_row(symbol=f"ALTERNATIVE{index}.DE", isin=isin,
                              name=f"Synthetische Position {index}"))
    return loader(monkeypatch, tmp_path, primary=primaries, rows=rows, cases=cases, mode="production")


def set_time(coordinator, moment):
    coordinator._clock = lambda: moment
    coordinator._cache.clock = lambda: moment


def quote_attempts(market):
    return [symbol for symbol, kind in market.calls if kind == "quote"]


def test_healthy_replacements_are_not_starved_by_three_failing_app_listings(monkeypatch, tmp_path):
    cases = {f"PRIMARY{i}.DE": {"quote_error": True} for i in (1, 2, 3)}
    cases |= {f"ALTERNATIVE{i}.DE": {"price": 220.0, "close": 200.0} for i in (1, 2, 3)}
    coordinator, _, market = three_listing_setup(monkeypatch, tmp_path, cases)
    for minutes in (0, 10, 20):  # first load plus two refreshes after the backoff has expired
        set_time(coordinator, NOW + timedelta(minutes=minutes))
        bundle = coordinator.load(refresh=minutes > 0)
        assert [p.instrument.provider_mapping.symbol for p in bundle.snapshot.positions] == [
            "ALTERNATIVE1.DE", "ALTERNATIVE2.DE", "ALTERNATIVE3.DE"]
        assert bundle.valuation.total_eur == Decimal(1320)
        for perfs in bundle.course_performances.values():
            assert [perf.percent for perf in perfs] == [Decimal(10)] * 3
    assert {s for s in quote_attempts(market) if s.startswith("ALTERNATIVE")} == {
        "ALTERNATIVE1.DE", "ALTERNATIVE2.DE", "ALTERNATIVE3.DE"}


def test_real_provider_outage_still_triggers_and_respects_provider_wide_backoff(monkeypatch, tmp_path):
    cases = {f"{kind}{i}.DE": {"quote_error": True}
             for kind in ("PRIMARY", "ALTERNATIVE") for i in (1, 2, 3)}
    coordinator, _, market = three_listing_setup(monkeypatch, tmp_path, cases)
    bundle = coordinator.load()
    assert bundle.available
    # Three consecutive provider failures open the breaker; nothing is fetched afterwards.
    assert quote_attempts(market) == ["PRIMARY1.DE", "ALTERNATIVE1.DE", "PRIMARY2.DE"]
    set_time(coordinator, NOW + timedelta(seconds=5))
    assert coordinator.load(refresh=True).available
    assert len(market.calls) == 3
    # After the backoff, fetching resumes (and reopens the breaker while the outage lasts).
    set_time(coordinator, NOW + timedelta(minutes=10))
    assert coordinator.load(refresh=True).available
    assert len(market.calls) > 3
