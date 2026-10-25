"""Invented private scenario records; no live responses or real owner choices."""

import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Barrier

import pytest
from test_fundamentals import AT, MONEY, REPORT, observation, period, report_set
from test_intrinsic_models import assumptions
from test_private_store import NOW, claims, config

import portfolio_dashboard.auth as auth
import portfolio_dashboard.private_store as storage
from portfolio_dashboard.config import Mode
from portfolio_dashboard.fundamentals import (
    EvidenceKind,
    FundamentalField,
    FundamentalRegistry,
    FundamentalUnit,
    ManualAdjustment,
    QualityStatus,
    UnitKind,
    WeightedSharesPeriod,
)
from portfolio_dashboard.intrinsic_models import InputReference, Scenario
from portfolio_dashboard.models import ValueStatus
from portfolio_dashboard.private_store import PrivateDatabase, PrivateStorageError
from portfolio_dashboard.scenario_codec import decode_document, encode_document
from portfolio_dashboard.scenarios import (
    ManualPriority,
    ManualPurpose,
    ManualValue,
    ScenarioConflict,
    ScenarioDocument,
    ScenarioRepository,
)
from portfolio_dashboard.watchlist import WatchlistEntry, WatchlistRepository

D = Decimal


def database(directory, mode=Mode.LOCAL):
    return PrivateDatabase(
        lambda: config(directory, mode), server_address=lambda: "127.0.0.1"
    )


def repository(directory):
    return ScenarioRepository(database(directory))


def document():
    originals = report_set()
    return ScenarioDocument(
        originals, originals.input_version, "synthetic-model-v1", AT
    )


def plan(doc, label=Scenario.BASE, version="synthetic-assumptions-v1"):
    return replace(assumptions(), inputs=doc.inputs, scenario=label, version=version)


def with_plan(doc):
    return doc.with_scenario(plan(doc), changed_at=AT)


def manual(doc, *, confirmed=True, purpose=ManualPurpose.NORMALIZATION):
    original = doc.originals.observations[0]
    replacement = replace(
        original,
        value=D("91.125"),
        provenance=replace(
            original.provenance,
            kind=EvidenceKind.MANUAL,
            field_reference="synthetic-private-evidence",
            raw_definition="synthetic explicitly adjusted operating profit",
            normalization="synthetic explicitly removed one-off",
            retrieved_at=None,
        ),
    )
    adjustment = ManualAdjustment(
        original,
        replacement,
        "synthetic normalization rationale",
        AT,
        AT if confirmed else None,
        "synthetic-owner-check" if confirmed else None,
    )
    return ManualValue(
        InputReference.from_report_set(doc.originals),
        adjustment,
        purpose,
        ManualPriority.MANUAL_WHEN_CONFIRMED,
    )


def amended(doc, *, confirmed=True):
    return doc.with_manual(
        manual(doc, confirmed=confirmed),
        data_version="synthetic-amended-v1",
        changed_at=AT,
    )


def codes(doc, label=Scenario.BASE):
    return {issue.code for issue in doc.issues_for(label) if issue.blocking}


def test_three_labels_round_trip_exact_versions_provenance_and_normalized_base(
    tmp_path,
):
    repo = repository(tmp_path / "private")
    doc = amended(document())
    for label in Scenario:
        doc = doc.with_scenario(plan(doc, label), changed_at=AT)
    saved = repo.save(doc)
    assert saved.revision == 1
    loaded = repository(tmp_path / "private").get(doc.originals.identity)
    assert loaded == saved
    assert loaded.model_version == "synthetic-model-v1"
    assert loaded.originals == doc.originals
    assert loaded.originals.observations[0].value == D(100)
    assert loaded.effective_report_set().effective_observations[0].value == D("91.125")
    assert (
        loaded.manual_values[0].adjustment.replacement.provenance.field_reference
        == "synthetic-private-evidence"
    )
    assert loaded.manual_values[0].priority is ManualPriority.MANUAL_WHEN_CONFIRMED
    assert {item.assumptions.scenario for item in loaded.scenarios} == set(Scenario)
    for label in Scenario:
        assert not codes(loaded, label)
        assert loaded.scenario(label).assumptions.safety_margin is None
    # No-op saves retain each label's version, even when labels share a version string.
    assert repo.save(saved).revision == 2


def test_restart_in_another_process_preserves_private_scenario_records(tmp_path):
    directory = tmp_path / "private"
    saved = repository(directory).save(with_plan(amended(document())))
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from portfolio_dashboard.config import AppConfig, CacheTTLs, LoginConfig, Mode, Provider, SourceConfig
from portfolio_dashboard.private_store import PrivateDatabase
from portfolio_dashboard.scenario_codec import decode_document
from portfolio_dashboard.scenarios import ScenarioRepository
from portfolio_dashboard.intrinsic_models import Scenario
config = AppConfig(Mode.LOCAL, SourceConfig(), Provider.SYNTHETIC, CacheTTLs(), LoginConfig(), sys.argv[1])
db = PrivateDatabase(lambda: config, server_address=lambda: '127.0.0.1')
with db.transaction() as connection:
    document = decode_document(connection.execute('SELECT payload FROM scenario_versions').fetchone()[0])
loaded = ScenarioRepository(db).get(document.originals.identity)
assert loaded.revision == 1
assert loaded.scenario(Scenario.BASE).assumptions.inputs == loaded.inputs
assert loaded.manual_values[0].adjustment.reason == 'synthetic normalization rationale'
print('scenario-restart-ok')
""",
            str(directory),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "scenario-restart-ok"
    assert saved.manual_values[0].adjustment.reason not in result.stdout + result.stderr


def test_new_instruments_and_missing_assumptions_never_receive_defaults(tmp_path):
    repo = repository(tmp_path / "private")
    doc = document()
    assert repo.get(doc.originals.identity) is None
    saved = repo.save(doc)
    assert saved.scenarios == () and saved.manual_values == ()
    assert codes(saved) == {"scenario_missing"}
    incomplete = replace(
        plan(saved),
        years=(),
        terminal=None,
        wacc=None,
        terminal_growth=None,
        sustainable_roic=None,
        confirmed_at=None,
        confirmation_reference=None,
    )
    saved = repo.save(saved.with_scenario(incomplete, changed_at=AT))
    loaded = repo.get(doc.originals.identity)
    assert loaded == saved
    assert loaded.scenario(Scenario.BASE).assumptions.wacc is None
    assert {
        issue.status for issue in loaded.issues_for(Scenario.BASE) if issue.blocking
    } == {QualityStatus.INCOMPLETE, QualityStatus.UNCONFIRMED_ASSUMPTIONS}
    assert not hasattr(loaded, "value_per_share")


def test_partial_year_and_terminal_components_stay_missing_after_reload(tmp_path):
    doc = document()
    assumption = plan(doc)
    assumption = replace(
        assumption,
        years=(replace(assumption.years[0], capex=None),) + assumption.years[1:],
        terminal=replace(assumption.terminal, nopat=None),
    )
    repo = repository(tmp_path / "private")
    loaded = repo.get(
        repo.save(doc.with_scenario(assumption, changed_at=AT)).originals.identity
    )
    assert loaded.scenario(Scenario.BASE).assumptions == assumption
    assert "scenario_components" in codes(loaded)


@pytest.mark.parametrize(
    "changes",
    [
        {"wacc": D("NaN")},
        {"wacc": D("Infinity")},
        {"wacc": 0.08},
        {"wacc": D(0)},
        {"wacc": D("0.01"), "terminal_growth": D("0.01")},
        {"wacc": D("0.01"), "terminal_growth": D("0.02")},
        {"safety_margin": D("-0.001")},
        {"safety_margin": D(1)},
        {"currency": "GBp"},
        {"scenario": "invented"},
        {"confirmed_at": None},
    ],
)
def test_invalid_scenario_values_rejected_before_any_saved_revision(tmp_path, changes):
    repo = repository(tmp_path / "private")
    doc = repo.save(document())
    with pytest.raises((TypeError, ValueError)):
        repo.save(doc.with_scenario(replace(plan(doc), **changes), changed_at=AT))
    assert repo.get(doc.originals.identity) == doc


@pytest.mark.parametrize("margin", [D(0), D("0.999999")])
def test_optional_safety_margin_accepts_only_fractional_valid_boundaries(
    tmp_path, margin
):
    doc = document()
    assumption = replace(plan(doc), safety_margin=margin)
    saved = repository(tmp_path / "private").save(
        doc.with_scenario(assumption, changed_at=AT)
    )
    assert saved.scenario(Scenario.BASE).assumptions.safety_margin == margin


def test_manual_reset_restores_automatic_source_and_preserves_revision_history(
    tmp_path,
):
    repo = repository(tmp_path / "private")
    original = repo.save(with_plan(document()))
    edited = repo.save(amended(original))
    assert "assumptions_input_mismatch" in codes(edited)
    assert edited.originals == original.originals
    assert (
        edited.effective_report_set().effective_observations[0]
        == edited.manual_values[0].adjustment.replacement
    )
    reset = repo.save(
        edited.reset_manual(
            edited.manual_values[0], data_version="synthetic-reset-v1", changed_at=AT
        )
    )
    assert reset.manual_values == ()
    assert (
        reset.effective_report_set().effective_observations
        == original.originals.observations
    )
    assert repo.get(original.originals.identity, revision=2) == edited
    assert repo.get(original.originals.identity, revision=1) == original
    assert repo.get(original.originals.identity, revision=4) is None


def test_manual_supplement_requires_explicit_confirmation_and_preserves_missing_original(
    tmp_path,
):
    base = document()
    missing = replace(
        base.originals.observations[0],
        value=None,
        status=ValueStatus.MISSING,
        unit=None,
    )
    doc = replace(
        base,
        originals=replace(
            base.originals, observations=(missing,) + base.originals.observations[1:]
        ),
    )
    prototype = manual(base, confirmed=False, purpose=ManualPurpose.SUPPLEMENT)
    addition = replace(
        prototype, adjustment=replace(prototype.adjustment, original=missing)
    )
    doc = doc.with_manual(
        addition, data_version="synthetic-supplement-v1", changed_at=AT
    )
    saved = repository(tmp_path / "private").save(with_plan(doc))
    assert saved.originals.observations[0] == missing
    assert saved.effective_report_set().effective_observations[0] == missing
    assert {"manual_confirmation", "ebit"} <= codes(saved)
    checked = replace(
        addition,
        adjustment=replace(
            addition.adjustment,
            confirmed_at=AT,
            confirmation_reference="synthetic-explicit-check",
        ),
    )
    checked_doc = saved.with_manual(
        checked, data_version="synthetic-supplement-v2", changed_at=AT
    )
    checked_doc = checked_doc.with_scenario(
        plan(checked_doc, version="synthetic-assumptions-v2"), changed_at=AT
    )
    loaded = repository(tmp_path / "private").save(checked_doc)
    assert not codes(loaded)
    assert loaded.originals.observations[0].value is None
    assert loaded.effective_report_set().effective_observations[0].value == D("91.125")


@pytest.mark.parametrize("change", ["new-data", "restatement", "new-period"])
def test_refresh_preserves_manual_value_but_never_applies_it_to_changed_basis(
    tmp_path, change
):
    repo = repository(tmp_path / "private")
    doc = repo.save(with_plan(amended(document())))
    originals = replace(doc.originals, input_version="synthetic-provider-v2")
    if change in {"restatement", "new-period"}:
        report = replace(REPORT, revision="synthetic-restatement-v2")
        observations = tuple(replace(o, report=report) for o in originals.observations)
        if change == "new-period":
            observations = (
                replace(observations[0], period=period(2025)),
            ) + observations[1:]
        originals = replace(originals, report=report, observations=observations)
    refreshed = repo.save(
        doc.refresh(originals, data_version="synthetic-refreshed-v2", changed_at=AT)
    )
    loaded = repo.get(doc.originals.identity)
    assert loaded == refreshed
    assert loaded.manual_values == doc.manual_values
    assert loaded.manual_values[0].needs_review(loaded.originals)
    assert loaded.effective_report_set().adjustments == ()
    assert (
        loaded.effective_report_set().effective_observations == originals.observations
    )
    assert {"manual_review_required", "assumptions_input_mismatch"} <= codes(loaded)
    assert loaded.scenario(Scenario.BASE) == doc.scenario(Scenario.BASE)
    assert (
        loaded.scenario(Scenario.BASE).assumptions.years
        == doc.scenario(Scenario.BASE).assumptions.years
    )
    assert repo.get(doc.originals.identity, revision=1) == doc
    with pytest.raises(ValueError, match="exact current"):
        loaded.with_manual(
            doc.manual_values[0], data_version="synthetic-v3", changed_at=AT
        )


def test_new_data_can_be_explicitly_rechecked_without_automatic_confirmation(tmp_path):
    repo = repository(tmp_path / "private")
    doc = repo.save(with_plan(amended(document())))
    refreshed = repo.save(
        doc.refresh(
            replace(doc.originals, input_version="synthetic-provider-v2"),
            data_version="synthetic-selected-v2",
            changed_at=AT,
        )
    )
    checked = refreshed.with_manual(
        manual(refreshed), data_version="synthetic-rechecked-v3", changed_at=AT
    )
    checked = checked.with_scenario(
        plan(checked, version="synthetic-assumptions-v2"), changed_at=AT
    )
    assert not codes(repo.save(checked))


def test_current_model_version_is_preserved_and_old_scenario_requires_review(tmp_path):
    repo = repository(tmp_path / "private")
    first = repo.save(with_plan(document()))
    changed = repo.save(replace(first, model_version="synthetic-model-v2"))
    assert "scenario_model_review_required" in codes(changed)
    assert changed.scenario(Scenario.BASE).model_version == "synthetic-model-v1"
    reviewed = changed.with_scenario(
        plan(changed, version="synthetic-assumptions-v2"), changed_at=AT
    )
    assert not codes(repo.save(reviewed))


def test_unchanged_original_refresh_does_not_overwrite_a_confirmed_manual_value(
    tmp_path,
):
    repo = repository(tmp_path / "private")
    doc = repo.save(with_plan(amended(document())))
    refreshed = repo.save(
        doc.refresh(doc.originals, data_version="synthetic-refreshed-v2", changed_at=AT)
    )
    assert not refreshed.manual_values[0].needs_review(refreshed.originals)
    assert refreshed.effective_report_set().effective_observations[0].value == D(
        "91.125"
    )
    assert "assumptions_input_mismatch" in codes(refreshed)


def test_registry_portfolio_and_watchlist_share_one_document_other_classes_stay_separate(
    tmp_path,
):
    doc = with_plan(document())
    other_listing = replace(
        doc.originals.identity,
        listing=replace(
            doc.originals.identity.listing,
            instrument=replace(
                doc.originals.identity.listing.instrument,
                trading_venue="synthetic-other-venue",
            ),
        ),
    )
    registry = FundamentalRegistry(
        (doc.originals.identity, other_listing), (doc.originals,)
    )
    watch = WatchlistEntry("synthetic-watch", "Synthetisch", other_listing.listing)
    db = database(tmp_path / "private")
    WatchlistRepository(db).add(watch)
    repo = ScenarioRepository(db)
    saved = repo.save(doc)
    assert registry.resolve(watch.instrument) is registry.resolve(
        doc.originals.identity.listing.instrument
    )
    assert repo.get(other_listing) == saved
    other_identity = replace(
        doc.originals.identity,
        share_class_id="synthetic-class-b",
        listing=replace(
            doc.originals.identity.listing,
            instrument=replace(
                doc.originals.identity.listing.instrument,
                instrument_id="synthetic-share-b",
            ),
        ),
    )
    assert repo.get(other_identity) is None
    other_doc = replace(
        document(), originals=replace(doc.originals, identity=other_identity)
    )
    other = repo.save(other_doc)
    assert repo.get(doc.originals.identity) == saved
    assert repo.get(other_identity) == other
    inconsistent = replace(other_identity, listing=doc.originals.identity.listing)
    with pytest.raises(ScenarioConflict):
        repo.get(inconsistent)
    with pytest.raises(ValueError, match="another security"):
        replace(other_doc, scenarios=doc.scenarios)


def test_two_concurrent_editors_have_one_winner_and_no_partial_revision(tmp_path):
    directory = tmp_path / "private"
    original = repository(directory).save(document())
    barrier = Barrier(2)

    def edit(label):
        repo = repository(directory)
        loaded = repo.get(original.originals.identity)
        changed = loaded.with_scenario(plan(loaded, label), changed_at=AT)
        barrier.wait(timeout=5)
        try:
            return repo.save(changed)
        except ScenarioConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(edit, (Scenario.BASE, Scenario.OPTIMISTIC)))
    (winner,) = (result for result in results if result is not None)
    assert winner.revision == 2
    assert len(winner.scenarios) == 1
    repo = repository(directory)
    assert repo.get(original.originals.identity) == winner
    assert repo.get(original.originals.identity, revision=1) == original
    assert repo.get(original.originals.identity, revision=3) is None
    # The losing editor reloads and intentionally incorporates its pending change.
    missing = next(
        label
        for label in (Scenario.BASE, Scenario.OPTIMISTIC)
        if winner.scenario(label) is None
    )
    merged = repo.save(winner.with_scenario(plan(winner, missing), changed_at=AT))
    assert merged.revision == 3 and len(merged.scenarios) == 2
    with pytest.raises(ScenarioConflict):
        repo.save(original)


def test_reused_versions_cannot_describe_different_values_or_bindings(tmp_path):
    repo = repository(tmp_path / "private")
    first = repo.save(with_plan(document()))
    assumption = replace(plan(first), safety_margin=D("0.2"))
    with pytest.raises(ValueError, match="assumptions version"):
        repo.save(first.with_scenario(assumption, changed_at=AT))
    with pytest.raises(ValueError, match="data version"):
        repo.save(
            replace(
                first,
                originals=replace(first.originals, input_version="synthetic-input-v2"),
            )
        )
    changed_originals = replace(first.originals, currency="USD", observations=())
    with pytest.raises(ValueError, match="original data version"):
        repo.save(
            replace(first, originals=changed_originals, data_version="synthetic-v2")
        )
    second = repo.save(
        first.with_scenario(
            replace(assumption, version="synthetic-assumptions-v2"), changed_at=AT
        )
    )
    with pytest.raises(ValueError, match="assumptions version"):
        repo.save(second.with_scenario(assumption, changed_at=AT))
    assert repo.get(first.originals.identity) == second


@pytest.mark.parametrize(
    "bad",
    [
        "unknown-unit",
        "wrong-unit",
        "wrong-currency",
        "missing-source",
        "missing-reason",
        "no-change-time",
        "nonfinite",
        "wrong-period",
    ],
)
def test_invalid_manual_data_and_missing_provenance_are_rejected(bad):
    doc = document()
    item = manual(doc)
    replacement = item.adjustment.replacement
    with pytest.raises((ValueError, TypeError)):
        if bad == "unknown-unit":
            replacement = replace(replacement, unit=None)
        elif bad == "wrong-unit":
            replacement = replace(
                replacement, unit=FundamentalUnit(UnitKind.SHARES, D(1))
            )
        elif bad == "wrong-currency":
            replacement = replace(replacement, unit=replace(MONEY, currency="USD"))
        elif bad == "missing-source":
            replacement = replace(
                replacement,
                provenance=replace(replacement.provenance, field_reference=""),
            )
        elif bad == "missing-reason":
            item = replace(item, adjustment=replace(item.adjustment, reason=""))
        elif bad == "no-change-time":
            item = replace(item, adjustment=replace(item.adjustment, changed_at=None))
        elif bad == "nonfinite":
            replacement = replace(replacement, value=D("NaN"))
        elif bad == "wrong-period":
            replacement = replace(replacement, period=period(2023))
        item = replace(
            item, adjustment=replace(item.adjustment, replacement=replacement)
        )
        doc.with_manual(item, data_version="synthetic-v2", changed_at=AT)


def test_wrong_binding_or_currency_and_duplicate_scenarios_are_rejected():
    doc = document()
    for reference in (
        replace(doc.inputs, instrument_id="synthetic-other"),
        replace(doc.inputs, share_class_id="synthetic-other"),
        replace(
            doc.inputs, report=replace(doc.inputs.report, issuer_id="synthetic-other")
        ),
        replace(doc.inputs, input_version="synthetic-other"),
    ):
        with pytest.raises(ValueError, match="exact current"):
            doc.with_scenario(replace(plan(doc), inputs=reference), changed_at=AT)
    with pytest.raises(ValueError, match="currency"):
        doc.with_scenario(replace(plan(doc), currency="USD"), changed_at=AT)
    saved = with_plan(doc)
    with pytest.raises(ValueError, match="duplicate scenario"):
        replace(saved, scenarios=saved.scenarios * 2)


def test_safe_codec_roundtrips_all_periods_missing_units_and_zero():
    doc = document()
    eps = observation(
        FundamentalField.WEIGHTED_EPS_SHARES,
        D(5),
        WeightedSharesPeriod(period(2024), "synthetic-split"),
    )
    missing = replace(
        observation(FundamentalField.PROVIDER_FCF),
        value=None,
        status=ValueStatus.MISSING,
        unit=None,
    )
    doc = replace(
        doc,
        originals=replace(
            doc.originals, observations=doc.originals.observations + (eps, missing)
        ),
    )
    doc = with_plan(amended(doc))
    assert decode_document(encode_document(doc)) == doc
    assert doc.scenario(Scenario.BASE).assumptions.terminal_growth == 0


@pytest.mark.parametrize(
    "kind",
    [
        "json",
        "duplicate",
        "nonfinite",
        "codec",
        "unknown-key",
        "wrong-type",
        "decimal-number",
        "decimal-nan",
        "identity",
        "revision",
        "period",
        "constructor",
    ],
)
def test_invalid_stored_payload_is_sanitized_and_never_looks_empty(
    tmp_path, caplog, kind
):
    repo = repository(tmp_path / "private")
    doc = repo.save(with_plan(amended(document())))
    payload = json.loads(encode_document(doc))
    if kind == "json":
        bad = "synthetic-private-invalid-json"
    elif kind == "duplicate":
        bad = '{"version": 1, "version": 1, "document": {}}'
    elif kind == "nonfinite":
        bad = '{"version": NaN, "document": {}}'
    else:
        record = payload["document"]
        if kind == "codec":
            payload["version"] = True
        elif kind == "unknown-key":
            record["synthetic-private-unknown"] = "synthetic-private-value"
        elif kind == "wrong-type":
            record["manual_values"] = {}
        elif kind == "decimal-number":
            record["scenarios"][0]["assumptions"]["wacc"] = 0.08
        elif kind == "decimal-nan":
            record["scenarios"][0]["assumptions"]["wacc"] = "NaN"
        elif kind == "identity":
            record["originals"]["identity"]["listing"]["instrument"][
                "instrument_id"
            ] = "synthetic-private-other"
        elif kind == "revision":
            record["revision"] = 99
        elif kind == "period":
            record["originals"]["shares_period"] = {
                "as_of": "synthetic-private-invalid-date"
            }
        elif kind == "constructor":
            record["__class__"] = "synthetic-private-command"
        bad = json.dumps(payload)
    with sqlite3.connect(tmp_path / "private" / "private.sqlite3") as connection:
        connection.execute("UPDATE scenario_versions SET payload = ?", (bad,))
    with pytest.raises(PrivateStorageError) as error:
        repo.get(doc.originals.identity)
    assert error.value.code == "data"
    assert "synthetic-private" not in str(error.value) + caplog.text
    assert "synthetic-private" not in repr(doc)
    assert "synthetic-private" not in repr(doc.manual_values[0])
    with pytest.raises(PrivateStorageError):
        repo.save(doc)


def legacy_database(directory):
    directory.mkdir(mode=0o700)
    path = directory / "private.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(storage.WATCHLIST_SCHEMA)
        connection.execute(f"PRAGMA application_id = {storage.APPLICATION_ID}")
        connection.execute("PRAGMA user_version = 1")
        connection.execute(
            "INSERT INTO watchlist VALUES ('synthetic-legacy', ?, NULL, 1)",
            (
                json.dumps(
                    {"version": 1, "label": "Synthetisch erhalten", "listing": None}
                ),
            ),
        )
    path.chmod(0o600)
    return path


def test_v1_migration_preserves_existing_watchlist_and_adds_versioned_scenarios(
    tmp_path,
):
    directory = tmp_path / "private"
    path = legacy_database(directory)
    repo = repository(directory)
    saved = repo.save(with_plan(document()))
    assert repo.get(saved.originals.identity) == saved
    assert WatchlistRepository(database(directory)).list() == (
        WatchlistEntry("synthetic-legacy", "Synthetisch erhalten"),
    )
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == storage.SCHEMA_VERSION
            == 2
        )
        assert (
            connection.execute("PRAGMA application_id").fetchone()[0]
            == storage.APPLICATION_ID
        )
        assert connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
        ).fetchall() == [("scenario_versions",), ("watchlist",)]


def test_legacy_migration_failure_leaves_all_prior_data_and_version_untouched(
    tmp_path, monkeypatch
):
    directory = tmp_path / "private"
    path = legacy_database(directory)
    original_migrations = storage.MIGRATIONS
    monkeypatch.setattr(
        storage,
        "MIGRATIONS",
        {
            1: original_migrations[1],
            2: (storage.SCENARIO_SCHEMA, "synthetic invalid SQL"),
        },
    )
    with pytest.raises(PrivateStorageError):
        repository(directory).save(document())
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT entry_id FROM watchlist").fetchall() == [
            ("synthetic-legacy",)
        ]
        assert connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table'"
        ).fetchall() == [("watchlist",)]
    monkeypatch.setattr(storage, "MIGRATIONS", original_migrations)
    assert repository(directory).save(document()).revision == 1


def test_legacy_schema_drift_is_rejected_before_migration_without_changes(tmp_path):
    directory = tmp_path / "private"
    path = legacy_database(directory)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE watchlist ADD COLUMN synthetic_unknown TEXT")
    before = path.read_bytes()
    with pytest.raises(PrivateStorageError) as error:
        repository(directory).get(document().originals.identity)
    assert error.value.code == "incompatible"
    assert path.read_bytes() == before


def test_failed_encoding_does_not_commit_any_part_of_the_new_revision(
    tmp_path, monkeypatch
):
    import portfolio_dashboard.scenario_codec as codec

    repo = repository(tmp_path / "private")
    first = repo.save(document())

    def fail(document):
        raise ValueError("synthetic encoding failure")

    monkeypatch.setattr(codec, "encode_document", fail)
    with pytest.raises(ValueError, match="synthetic encoding failure"):
        repo.save(with_plan(first))
    assert repo.get(first.originals.identity) == first
    assert repo.get(first.originals.identity, revision=2) is None


def test_current_owner_gate_is_rechecked_for_every_scenario_read_and_write(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(auth.time, "time", lambda: NOW)
    user = claims()
    db = PrivateDatabase(
        lambda: config(tmp_path / "private", Mode.PRODUCTION), user=lambda: user
    )
    repo = ScenarioRepository(db)
    saved = repo.save(document())
    user["exp"] = NOW

    def forbidden(*args):
        raise AssertionError("access after expired authorization")

    monkeypatch.setattr(storage, "_database_path", forbidden)
    for operation in (
        lambda: repo.get(saved.originals.identity),
        lambda: repo.get(saved.originals.identity, revision=1),
        lambda: repo.save(saved),
    ):
        with pytest.raises(PrivateStorageError) as error:
            operation()
        assert error.value.code == "access"


def test_local_gate_and_demo_separation_apply_to_scenarios(tmp_path):
    doc = document()
    denied = ScenarioRepository(
        PrivateDatabase(
            lambda: config(tmp_path / "private"), server_address=lambda: "0.0.0.0"
        )
    )
    for operation in (
        lambda: denied.get(doc.originals.identity),
        lambda: denied.save(doc),
    ):
        with pytest.raises(PrivateStorageError) as error:
            operation()
        assert error.value.code == "local"
    first = database(tmp_path / "private", Mode.DEMO)
    second = database(tmp_path / "private", Mode.DEMO)
    ScenarioRepository(first).save(with_plan(doc))
    assert ScenarioRepository(second).get(doc.originals.identity) is None
    first.close()
    second.close()
    assert not (tmp_path / "private").exists()
