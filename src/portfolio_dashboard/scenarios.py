"""Private, immutable scenario revisions; no defaults, provider IO or valuation.

Original observations, manual normalizations and future plans remain separate.
Only exact confirmed bindings participate in the effective report selection.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum

from portfolio_dashboard.fundamentals import (
    FundamentalReportSet,
    ManualAdjustment,
    QualityIssue,
    QualityStatus,
    VerifiedFundamentalIdentity,
    _tuple,
)
from portfolio_dashboard.intrinsic_models import (
    InputReference,
    Scenario,
    ScenarioAssumptions,
)
from portfolio_dashboard.models import ValueStatus, _aware, _text
from portfolio_dashboard.private_store import PrivateDatabase, PrivateStorageError


class ManualPurpose(Enum):
    SUPPLEMENT = "supplement"
    NORMALIZATION = "normalization"


class ManualPriority(Enum):
    MANUAL_WHEN_CONFIRMED = "manual_when_confirmed"


def _same_security(left: InputReference, right: InputReference) -> bool:
    return (
        left.instrument_id,
        left.share_class_id,
        left.report.issuer_id,
        left.report.consolidation_scope,
    ) == (
        right.instrument_id,
        right.share_class_id,
        right.report.issuer_id,
        right.report.consolidation_scope,
    )


@dataclass(frozen=True, slots=True, repr=False)
class ManualValue:
    """Explicit priority never implies confirmation; provenance stays private."""

    inputs: InputReference
    adjustment: ManualAdjustment
    purpose: ManualPurpose
    priority: ManualPriority

    def __post_init__(self) -> None:
        if not isinstance(self.inputs, InputReference):
            raise TypeError("inputs must be InputReference")
        if not isinstance(self.adjustment, ManualAdjustment):
            raise TypeError("adjustment must be ManualAdjustment")
        if not isinstance(self.purpose, ManualPurpose):
            raise TypeError("purpose must be ManualPurpose")
        if not isinstance(self.priority, ManualPriority):
            raise TypeError("priority must be ManualPriority")
        if self.adjustment.original.report != self.inputs.report:
            raise ValueError("manual value requires its original report binding")
        replacement = self.adjustment.replacement
        if (
            replacement.status is not ValueStatus.AVAILABLE
            or replacement.unit is None
            or not replacement.unit.known
        ):
            raise ValueError("manual value requires a number and explicit known unit")

    def needs_review(self, originals: FundamentalReportSet) -> bool:
        return (
            self.inputs != InputReference.from_report_set(originals)
            or self.adjustment.original not in originals.observations
        )


@dataclass(frozen=True, slots=True, repr=False)
class SavedScenario:
    assumptions: ScenarioAssumptions
    model_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.assumptions, ScenarioAssumptions):
            raise TypeError("assumptions must be ScenarioAssumptions")
        _text(self.model_version, "model_version")


@dataclass(frozen=True, slots=True, repr=False)
class ScenarioDocument:
    """One editor snapshot. Revision zero is new; saved revisions are immutable.

    data_version identifies the selected input including manual edits/resets.
    originals.input_version identifies the untouched provider/report snapshot.
    No plan is created, rolled forward or confirmed by these operations.
    """

    originals: FundamentalReportSet = field(repr=False)
    data_version: str
    model_version: str
    changed_at: datetime
    manual_values: tuple[ManualValue, ...] = field(default=(), repr=False)
    scenarios: tuple[SavedScenario, ...] = field(default=(), repr=False)
    revision: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.originals, FundamentalReportSet):
            raise TypeError("originals must be FundamentalReportSet")
        if self.originals.adjustments:
            raise ValueError("original observations and manual values must be separate")
        _text(self.data_version, "data_version")
        _text(self.model_version, "model_version")
        _aware(self.changed_at, "changed_at")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative integer")
        _tuple(self.manual_values, ManualValue, "manual_values")
        _tuple(self.scenarios, SavedScenario, "scenarios")
        if self.manual_values and self.data_version == self.originals.input_version:
            raise ValueError("manual inputs require a separate selected data version")
        reference = self.inputs
        keys = set()
        for manual in self.manual_values:
            if not _same_security(manual.inputs, reference):
                raise ValueError("manual value belongs to another security")
            key = (manual.adjustment.original.field, manual.adjustment.original.period)
            if key in keys:
                raise ValueError("duplicate manual field and period")
            keys.add(key)
        labels = set()
        for saved in self.scenarios:
            if not _same_security(saved.assumptions.inputs, reference):
                raise ValueError("scenario belongs to another security")
            if saved.assumptions.scenario in labels:
                raise ValueError("duplicate scenario label")
            labels.add(saved.assumptions.scenario)
        # Also validate currencies, dates and original membership of active edits.
        self.effective_report_set()

    @property
    def inputs(self) -> InputReference:
        return replace(
            InputReference.from_report_set(self.originals),
            input_version=self.data_version,
        )

    def effective_report_set(self) -> FundamentalReportSet:
        """Normalized input, separate from originals; pending review blocks use."""
        active = tuple(
            m.adjustment
            for m in self.manual_values
            if not m.needs_review(self.originals)
        )
        pending = any(m.needs_review(self.originals) for m in self.manual_values)
        issues = self.originals.qualifications
        if pending:
            issues += (
                QualityIssue(
                    QualityStatus.UNCONFIRMED_ASSUMPTIONS, "manual_review_required"
                ),
            )
        return replace(
            self.originals,
            input_version=self.data_version,
            adjustments=active,
            qualifications=issues,
        )

    def scenario(self, label: Scenario) -> SavedScenario | None:
        if not isinstance(label, Scenario):
            raise TypeError("label must be Scenario")
        return next(
            (s for s in self.scenarios if s.assumptions.scenario is label), None
        )

    def issues_for(self, label: Scenario) -> tuple[QualityIssue, ...]:
        saved = self.scenario(label)
        if saved is None:
            return (QualityIssue(QualityStatus.INCOMPLETE, "scenario_missing"),)
        issues = saved.assumptions.issues_for(self.effective_report_set())
        if saved.model_version != self.model_version:
            issues += (
                QualityIssue(
                    QualityStatus.UNCONFIRMED_ASSUMPTIONS,
                    "scenario_model_review_required",
                ),
            )
        return issues

    def with_scenario(
        self, assumptions: ScenarioAssumptions, *, changed_at: datetime
    ) -> "ScenarioDocument":
        if not isinstance(assumptions, ScenarioAssumptions):
            raise TypeError("assumptions must be ScenarioAssumptions")
        if (
            assumptions.inputs != self.inputs
            or assumptions.currency != self.originals.currency
        ):
            raise ValueError(
                "scenario requires the exact current data binding and currency"
            )
        retained = tuple(
            s
            for s in self.scenarios
            if s.assumptions.scenario is not assumptions.scenario
        )
        return replace(
            self,
            scenarios=retained + (SavedScenario(assumptions, self.model_version),),
            changed_at=changed_at,
        )

    def with_manual(
        self, manual: ManualValue, *, data_version: str, changed_at: datetime
    ) -> "ScenarioDocument":
        if not isinstance(manual, ManualValue) or manual.needs_review(self.originals):
            raise ValueError("manual value requires the exact current original binding")
        key = (manual.adjustment.original.field, manual.adjustment.original.period)
        retained = tuple(
            m
            for m in self.manual_values
            if (m.adjustment.original.field, m.adjustment.original.period) != key
        )
        self._new_data_version(data_version)
        return replace(
            self,
            manual_values=retained + (manual,),
            data_version=data_version,
            changed_at=changed_at,
        )

    def reset_manual(
        self, manual: ManualValue, *, data_version: str, changed_at: datetime
    ) -> "ScenarioDocument":
        if manual not in self.manual_values:
            raise ValueError("manual value is not selected")
        self._new_data_version(data_version)
        return replace(
            self,
            manual_values=tuple(m for m in self.manual_values if m != manual),
            data_version=data_version,
            changed_at=changed_at,
        )

    def refresh(
        self,
        originals: FundamentalReportSet,
        *,
        data_version: str,
        changed_at: datetime,
    ) -> "ScenarioDocument":
        if not isinstance(originals, FundamentalReportSet):
            raise TypeError("originals must be FundamentalReportSet")
        if originals.identity.data_key != self.originals.identity.data_key:
            raise ValueError("refresh cannot change the verified security or source")
        self._new_data_version(data_version)
        return replace(
            self, originals=originals, data_version=data_version, changed_at=changed_at
        )

    def _new_data_version(self, version: str) -> None:
        _text(version, "data_version")
        if version == self.data_version:
            raise ValueError("input changes require a new data version")


class ScenarioConflict(ValueError):
    """A stale editor must reload; no private identifiers in the message."""


class ScenarioRepository:
    """Append-only revisions with comparison and insert in the same transaction."""

    def __init__(self, database: PrivateDatabase):
        self._database = database

    def get(
        self, identity: VerifiedFundamentalIdentity, *, revision: int | None = None
    ) -> ScenarioDocument | None:
        with self._database.transaction() as connection:
            if not isinstance(identity, VerifiedFundamentalIdentity):
                raise TypeError("identity must be VerifiedFundamentalIdentity")
            if revision is not None and (type(revision) is not int or revision < 1):
                raise ValueError("revision must be a positive integer")
            row = connection.execute(
                "SELECT revision, payload FROM scenario_versions WHERE instrument_id = ? "
                + (
                    "ORDER BY revision DESC LIMIT 1"
                    if revision is None
                    else "AND revision = ?"
                ),
                (identity.listing.instrument.instrument_id,)
                if revision is None
                else (identity.listing.instrument.instrument_id, revision),
            ).fetchone()
            if row is None:
                return None
            document = self._decode(row, identity.listing.instrument.instrument_id)
            self._identity(document, identity)
            return document

    def save(self, document: ScenarioDocument) -> ScenarioDocument:
        from portfolio_dashboard.scenario_codec import encode_document

        with self._database.transaction() as connection:
            if not isinstance(document, ScenarioDocument):
                raise TypeError("document must be ScenarioDocument")
            key = document.inputs.instrument_id
            rows = connection.execute(
                "SELECT revision, payload FROM scenario_versions WHERE instrument_id = ? ORDER BY revision",
                (key,),
            ).fetchall()
            previous = tuple(self._decode(row, key) for row in rows)
            expected = previous[-1].revision if previous else 0
            if document.revision != expected:
                raise ScenarioConflict(
                    "Daten wurden geändert. Erneut laden und Änderungen prüfen."
                )
            if previous:
                self._identity(previous[-1], document.originals.identity)
                self._versions(document, previous)
                if document.changed_at < previous[-1].changed_at:
                    raise ValueError("change time cannot precede saved revision")
            saved = replace(document, revision=expected + 1)
            connection.execute(
                "INSERT INTO scenario_versions (instrument_id, revision, payload) VALUES (?, ?, ?)",
                (key, saved.revision, encode_document(saved)),
            )
            return saved

    @staticmethod
    def _identity(
        document: ScenarioDocument, identity: VerifiedFundamentalIdentity
    ) -> None:
        if document.originals.identity.data_key != identity.data_key:
            raise ScenarioConflict(
                "Gespeicherte Instrumentzuordnung widerspricht der geprüften Identität."
            )

    @staticmethod
    def _versions(
        document: ScenarioDocument, previous: tuple[ScenarioDocument, ...]
    ) -> None:
        for old in previous:
            if document.data_version == old.data_version and (
                document.originals != old.originals
                or document.manual_values != old.manual_values
            ):
                raise ValueError("changed inputs require an unused data version")
            if (
                document.originals.input_version == old.originals.input_version
                and document.originals != old.originals
            ):
                raise ValueError(
                    "changed originals require an unused original data version"
                )
            for saved in document.scenarios:
                for former in old.scenarios:
                    if (
                        saved.assumptions.scenario is former.assumptions.scenario
                        and saved.assumptions.version == former.assumptions.version
                        and saved != former
                    ):
                        raise ValueError(
                            "changed assumptions require an unused assumptions version"
                        )

    @staticmethod
    def _decode(row: tuple, key: str) -> ScenarioDocument:
        from portfolio_dashboard.scenario_codec import decode_document

        document = decode_document(row[1])
        if document.revision != row[0] or document.inputs.instrument_id != key:
            raise PrivateStorageError("data")
        return document
