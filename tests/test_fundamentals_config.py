"""Private fundamentals mapping: strict parsing with invented entries only."""

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import portfolio_dashboard.fundamentals_loader as loader_module
from portfolio_dashboard.config import ConfigurationError, Mode, load_config
from portfolio_dashboard.fundamentals import IssuerKind
from portfolio_dashboard.fundamentals_config import (
    FundamentalsConfigError,
    load_fundamental_subjects,
    parse_fundamental_subjects,
)
from portfolio_dashboard.fundamentals_normalization import OwnerConfirmations
from portfolio_dashboard.models import Instrument, InstrumentType, QuoteUnit

MOMENT = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def entry(**overrides):
    values = {
        "isin": "XX0000000001",
        "venue": "SYN",
        "symbol": "SYN.A",
        "trading_currency": "EUR",
        "equity_form": "share",
        "issuer_id": "synthetic-issuer-a",
        "share_class_id": "synthetic-class-a",
        "consolidation_scope": "synthetic-group",
        "issuer_kind": "nonfinancial",
        "evidence_reference": "synthetic-proof",
        "verified_at": MOMENT,
        "reporting_currency": "EUR",
        "fiscal_year_end": "12-31",
        "ordinary_share_classes": 1,
        "class_count_evidence": "synthetic-class-proof",
        "class_count_verified_at": MOMENT,
    }
    values.update(overrides)
    return {k: v for k, v in values.items() if v is not None}


def instrument(isin="XX0000000001", venue="SYN"):
    return Instrument(
        InstrumentType.STOCK,
        instrument_id=isin,
        trading_venue=venue,
        quote_unit=QuoteUnit("EUR", Decimal(1)),
    )


def test_a_complete_entry_becomes_a_verified_subject():
    subjects = parse_fundamental_subjects([entry()])
    subject = subjects.resolve(instrument())
    assert subject is not None and len(subjects) == 1
    assert subject.identity.source.entity_id == "SYN.A"
    assert subject.identity.issuer_kind is IssuerKind.NONFINANCIAL
    assert subject.expectation.reporting_currency == "EUR"
    assert (
        subject.expectation.fiscal_year_end_month,
        subject.expectation.fiscal_year_end_day,
    ) == (12, 31)
    assert subject.confirmations == OwnerConfirmations()  # Unconfirmed by default.
    assert subject.class_count == 1
    assert subject.capital_structure(MOMENT.date()).issues == ()


def test_datetimes_may_be_iso_strings_and_confirmations_are_explicit():
    subjects = parse_fundamental_subjects(
        [
            entry(
                verified_at="2026-10-01T09:00:00+00:00",
                confirm_ebit_definition=True,
                confirm_lease_treatment=True,
                quote_unit_scale=1,
            )
        ]
    )
    subject = subjects.resolve(instrument())
    assert subject.confirmations == OwnerConfirmations(True, False, True)
    assert subject.identity.verified_at == MOMENT


def test_unknown_class_count_stays_unknown_and_blocks():
    subject = parse_fundamental_subjects(
        [
            entry(
                ordinary_share_classes=None,
                class_count_evidence=None,
                class_count_verified_at=None,
            )
        ]
    ).resolve(instrument())
    assert subject.class_count is None
    assert [i.code for i in subject.capital_structure(MOMENT.date()).issues] == [
        "ordinary_class_count_unknown"
    ]


def test_lookup_is_exact_by_id_and_venue_never_by_name_or_ticker():
    subjects = parse_fundamental_subjects([entry()])
    assert subjects.resolve(instrument("XX0000000002")) is None
    assert subjects.resolve(instrument(venue="OTHER")) is None
    named = Instrument(InstrumentType.STOCK, display_ticker="SYN.A")
    assert subjects.resolve(named) is None


def test_listings_of_one_security_must_agree_in_owner_inputs():
    second = entry(venue="SYN2", symbol="SYN.A2", source_symbol="SYN.A")
    first = entry(source_symbol="SYN.A")
    subjects = parse_fundamental_subjects([first, second])
    one, two = (
        subjects.resolve(instrument()),
        subjects.resolve(instrument(venue="SYN2")),
    )
    assert one.fetch_key == two.fetch_key  # One retrieval for both listings.
    with pytest.raises(FundamentalsConfigError, match="widersprechen"):
        parse_fundamental_subjects(
            [
                first,
                entry(
                    venue="SYN2",
                    symbol="SYN.A2",
                    source_symbol="SYN.A",
                    reporting_currency="USD",
                ),
            ]
        )


@pytest.mark.parametrize(
    "bad",
    [
        {"isin": None},
        {"verified_at": None},
        {"unknown_key": "x"},
        {"verified_at": datetime(2026, 10, 1)},  # Naive: no assumed time zone.
        {"verified_at": "yesterday"},
        {"issuer_kind": "casino"},
        {"equity_form": "unit"},
        {"fiscal_year_end": "13-40"},
        {"fiscal_year_end": "1231"},
        {"reporting_currency": "eur"},
        {"trading_currency": "euro"},
        {"quote_unit_scale": True},
        {"quote_unit_scale": 0},
        {"ordinary_share_classes": 0},
        {"ordinary_share_classes": "1"},
        {"ordinary_share_classes": 1, "class_count_evidence": None},
        {"ordinary_share_classes": 1, "class_count_verified_at": None},
        {"confirm_ebit_definition": "yes"},
        {"isin": "  "},
    ],
)
def test_incomplete_or_doubtful_entries_raise_without_echoing_values(bad):
    with pytest.raises(FundamentalsConfigError) as error:
        parse_fundamental_subjects([entry(**bad)])
    message = str(error.value)
    assert "Eintrag 1" in message
    for secret in ("XX0000000001", "SYN.A", "synthetic-issuer-a", "casino"):
        assert secret not in message


def test_conflicting_or_duplicate_entries_are_rejected():
    for entries in (
        [entry(), entry()],  # Same listing twice.
        [
            entry(),
            entry(isin="XX0000000002", symbol="SYN.B"),
        ],  # One issuer, two classes.
        [
            entry(),
            entry(isin="XX0000000002", symbol="SYN.A", venue="SYN2"),
        ],  # Symbol reuse.
    ):
        with pytest.raises(FundamentalsConfigError):
            parse_fundamental_subjects(entries)


def test_non_list_input_is_rejected():
    for bad in (None, {"fundamental": []}, "x", 3):
        with pytest.raises(FundamentalsConfigError):
            parse_fundamental_subjects(bad)
    assert len(parse_fundamental_subjects([])) == 0
    with pytest.raises(FundamentalsConfigError, match="Eintrag 1"):
        parse_fundamental_subjects(["not-a-mapping"])


def test_file_loading_reports_no_path_or_content(tmp_path):
    good = tmp_path / "fundamentals.toml"
    good.write_text(
        "[[fundamental]]\n"
        'isin = "XX0000000001"\nvenue = "SYN"\nsymbol = "SYN.A"\n'
        'trading_currency = "EUR"\nequity_form = "share"\n'
        'issuer_id = "i"\nshare_class_id = "c"\nconsolidation_scope = "g"\n'
        'issuer_kind = "nonfinancial"\nevidence_reference = "p"\n'
        "verified_at = 2026-10-01T09:00:00Z\n"
        'reporting_currency = "EUR"\nfiscal_year_end = "12-31"\n'
    )
    assert len(load_fundamental_subjects(str(good))) == 1
    broken = tmp_path / "secret-broken-name.toml"
    broken.write_text("[[fundamental\nsynthetic-secret-content")
    for path in (broken, tmp_path / "secret-missing-name.toml"):
        with pytest.raises(FundamentalsConfigError) as error:
            load_fundamental_subjects(str(path))
        assert "secret" not in str(error.value)


def test_file_that_is_not_utf8_gives_only_the_safe_message(tmp_path):
    binary = tmp_path / "secret-binary-name.toml"
    binary.write_bytes(b'[[fundamental]]\nsecret = "\xff\xfe\x80"\n')
    with pytest.raises(FundamentalsConfigError) as error:
        load_fundamental_subjects(str(binary))
    assert str(error.value) == (
        "Fundamentaldaten-Zuordnungsdatei fehlt oder ist ungültig."
    )
    assert error.value.__cause__ is None and error.value.__suppress_context__


def test_ttl_and_file_settings_in_the_runtime_configuration():
    assert load_config({}).ttls.fundamentals_seconds == 86_400
    values = {"PORTFOLIO_FUNDAMENTALS_TTL_SECONDS": "3600"}
    assert load_config(values).ttls.fundamentals_seconds == 3600
    for invalid in ("0", "-5", "1.5", "synthetic-secret"):
        with pytest.raises(
            ConfigurationError, match="PORTFOLIO_FUNDAMENTALS_TTL_SECONDS"
        ) as error:
            load_config({"PORTFOLIO_FUNDAMENTALS_TTL_SECONDS": invalid})
        assert invalid not in str(error.value)
    local = {
        "PORTFOLIO_MODE": "local",
        "PORTFOLIO_SHEET_ID": "synthetic-source-id",
        "PORTFOLIO_SHEET_RANGE": "'Synthetic tab'!A1:M10",
        "PORTFOLIO_SHEETS_CREDENTIALS_FILE": "/nonexistent/key.json",
        "PORTFOLIO_FUNDAMENTALS_FILE": "/nonexistent/fundamentals.toml",
    }
    config = load_config(local)
    assert config.mode is Mode.LOCAL
    assert config.source.fundamentals_file == local["PORTFOLIO_FUNDAMENTALS_FILE"]
    assert "fundamentals.toml" not in repr(config)
    assert (
        load_config(
            local | {"PORTFOLIO_FUNDAMENTALS_FILE": ""}
        ).source.fundamentals_file
        is None
    )
    with pytest.raises(ConfigurationError, match="PORTFOLIO_FUNDAMENTALS_FILE"):
        load_config(local | {"PORTFOLIO_FUNDAMENTALS_FILE": "<PLACEHOLDER>"})
    # Demo discards the real mapping like every other real source setting.
    demo = load_config({"PORTFOLIO_FUNDAMENTALS_FILE": "/x", "PORTFOLIO_MODE": "demo"})
    assert demo.source.fundamentals_file is None


# --- wiring in the app (session key; no retrieval on construction) -------------------


APP_PATH = Path(__file__).parents[1] / "app.py"


@pytest.fixture
def local_app(monkeypatch, tmp_path):
    import portfolio_dashboard.local_source as local_source
    from portfolio_dashboard.demo import demo_snapshot
    from portfolio_dashboard.quote_cache import QuoteCache
    from portfolio_dashboard.refresh import PortfolioRefreshCoordinator

    for name in tuple(os.environ):
        if name.startswith("PORTFOLIO_"):
            monkeypatch.delenv(name)
    env = {
        "PORTFOLIO_MODE": "local",
        "PORTFOLIO_SHEET_ID": "synthetic-source-id",
        "PORTFOLIO_SHEET_RANGE": "'Synthetic tab'!A1:M10",
        "PORTFOLIO_SHEETS_CREDENTIALS_FILE": "/nonexistent/key.json",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    class Silent:
        def quote(self, *args, **kwargs):
            raise AssertionError("no quote expected")

        history = fx = quote

    monkeypatch.setattr(
        local_source,
        "build_local_coordinator",
        lambda config: PortfolioRefreshCoordinator(
            lambda attempted_at: demo_snapshot(), QuoteCache(Silent())
        ),
    )
    original = st.get_option
    monkeypatch.setattr(
        st,
        "get_option",
        lambda key: "127.0.0.1" if key == "server.address" else original(key),
    )
    return monkeypatch, tmp_path


def test_app_without_a_mapping_keeps_no_loader_and_works(local_app):
    app = AppTest.from_file(str(APP_PATH)).run()
    assert not app.exception
    assert app.session_state[loader_module.LOADER_STATE_KEY] is None
    assert app.title


def test_app_builds_a_loader_after_the_gate_without_retrieving(local_app, monkeypatch):
    monkeypatch, tmp_path = local_app
    path = tmp_path / "fundamentals.toml"
    path.write_text(
        "[[fundamental]]\n"
        'isin = "XX0000000001"\nvenue = "SYN"\nsymbol = "SYN.A"\n'
        'trading_currency = "EUR"\nequity_form = "share"\n'
        'issuer_id = "i"\nshare_class_id = "c"\nconsolidation_scope = "g"\n'
        'issuer_kind = "nonfinancial"\nevidence_reference = "p"\n'
        "verified_at = 2026-10-01T09:00:00Z\n"
        'reporting_currency = "EUR"\nfiscal_year_end = "12-31"\n'
    )
    monkeypatch.setenv("PORTFOLIO_FUNDAMENTALS_FILE", str(path))
    import portfolio_dashboard.fundamentals_provider as provider_module

    def forbidden(self, *args, **kwargs):
        raise AssertionError("construction must not contact the provider")

    monkeypatch.setattr(
        provider_module.YFinanceFundamentalsProvider, "fetch", forbidden
    )
    app = AppTest.from_file(str(APP_PATH)).run()
    assert not app.exception
    held = app.session_state[loader_module.LOADER_STATE_KEY]
    assert isinstance(held, loader_module.FundamentalsLoader)
    app.run()
    assert (
        app.session_state[loader_module.LOADER_STATE_KEY] is held
    )  # Once per session.


def test_app_shows_only_a_safe_message_for_a_broken_mapping(local_app, monkeypatch):
    monkeypatch, tmp_path = local_app
    path = tmp_path / "secret-name.toml"
    path.write_text("[[fundamental]]\nisin = 'synthetic-secret'\n")
    monkeypatch.setenv("PORTFOLIO_FUNDAMENTALS_FILE", str(path))
    app = AppTest.from_file(str(APP_PATH)).run()
    assert not app.exception
    assert "Eintrag 1" in app.error[0].value
    assert "secret" not in app.error[0].value
    assert not app.title
