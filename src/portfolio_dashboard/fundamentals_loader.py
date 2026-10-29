"""Protected, sparing fundamentals loading with a separate cache (M8-05, #74).

``FundamentalsLoader.load(instruments, refresh=...)`` is the one entry point for
the portfolio and the Watchlist. It is called on use only: there is no scheduler
or background service. The loader

- checks access first: the validated owner in production, the loopback address in
  local mode. Demo and anonymous or foreign sessions are refused before the cache
  is read and before any provider is created or called. The check repeats before
  every provider call; no result is cached;
- resolves each instrument to an owner-verified identity (exact id, type and
  venue; names and tickers are never keys) and deduplicates by issuer, share class,
  security, source and scope, so portfolio and Watchlist share one retrieval;
- keeps the raw retrieval in the protected cache and repeats the deterministic
  normalization on every load;
- retrieves a security at most once per retrieval window (24 h by default) and at
  most ``max_fetches_per_load`` securities per call, with pauses, capped
  exponential backoff and no retry after a restriction. A manual refresh skips only
  the window, never backoff, a minimum interval or the batch limit;
- never combines retrievals: a failed or partial retrieval keeps the last complete
  one, flagged, and an empty cache shows no data instead of an invented state;
- separates the *age of the last retrieval* from the *age of the business reports*:
  a new retrieval of old reports does not make them recent (``issues_at`` and
  ``latest_period_end`` judge the reports).
"""

import importlib.metadata
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from portfolio_dashboard.config import AppConfig, CacheTTLs
from portfolio_dashboard.fundamentals import (
    FlowPeriod,
    FundamentalReportSet,
    QualityIssue,
    VerifiedFundamentalIdentity,
)
from portfolio_dashboard.fundamentals_cache import (
    CODE_PATTERN,
    CacheRecord,
    FailedAttempt,
    FundamentalsStore,
    PrivateFundamentalsStore,
    StoredFetch,
    store_key,
)
from portfolio_dashboard.fundamentals_config import (
    FundamentalSubject,
    FundamentalSubjects,
    load_fundamental_subjects,
    parse_fundamental_subjects,
)
from portfolio_dashboard.fundamentals_normalization import (
    FAILURE_ACCESS_RESTRICTED,
    NormalizedFundamentals,
    ProviderStatements,
    build_report_set,
    normalize_statements,
)
from portfolio_dashboard.models import Instrument, ValueStatus

if TYPE_CHECKING:
    from portfolio_dashboard.fundamentals_provider import FundamentalsProvider

LOADER_STATE_KEY = "own.fundamentals_loader"

# Notes: why a shown state is not a plain current one, or why nothing was fetched.
NOTE_WINDOW_EXPIRED = "retrieval_window_expired"
NOTE_REFRESH_FAILED = "refresh_failed"
NOTE_PACKAGE_CHANGED = "package_version_changed"
NOTE_BACKOFF = "backoff_active"
NOTE_TOO_SOON = "manual_refresh_too_soon"
NOTE_DEFERRED = "deferred_batch_limit"
NOTE_PROVIDER_BACKOFF = "provider_backoff"
NOTE_REPORT_SET_REJECTED = "report_set_rejected"
NOTE_UNCHANGED = "content_unchanged"
_STALE_NOTES = frozenset(
    {NOTE_WINDOW_EXPIRED, NOTE_REFRESH_FAILED, NOTE_PACKAGE_CHANGED}
)

_DATASET_FOR_PART = (
    ("annual_income", "annual_income"),
    ("annual_balance", "annual_balance"),
    ("annual_cashflow", "annual_cashflow"),
    ("info", "info"),
    ("dated_shares", "shares_full"),
)


class FundamentalsStatus(StrEnum):
    CURRENT = "current"  # Last retrieval within the window; reports may still be old.
    CACHED = "cached"  # Last complete retrieval shown, flagged (see ``notes``).
    UNAVAILABLE = "unavailable"  # No complete retrieval stored; nothing is invented.
    UNSUPPORTED = "unsupported"  # Identity outside the model; never retrieved.
    UNMAPPED = "unmapped"  # No owner-verified fundamentals identity for the listing.


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    """Limits for retrievals; the defaults are the owner-reviewed starting values."""

    ttl_seconds: int = 86_400  # Retrieval window, 24 h.
    min_refetch_seconds: int = 3_600  # A manual refresh respects this minimum age.
    backoff_base_seconds: int = 300  # After a failure: doubles per consecutive one.
    backoff_max_seconds: int = 21_600
    restricted_base_seconds: int = (
        3_600  # After a restriction (throttling, 401/403/429).
    )
    restricted_max_seconds: int = 86_400
    pause_seconds: float = 3.0  # Between securities; the adapter pauses within one.
    max_fetches_per_load: int = 5

    def __post_init__(self) -> None:
        for name in (
            "ttl_seconds",
            "min_refetch_seconds",
            "backoff_base_seconds",
            "backoff_max_seconds",
            "restricted_base_seconds",
            "restricted_max_seconds",
            "max_fetches_per_load",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.pause_seconds < 0:
            raise ValueError("pause_seconds must be nonnegative")
        if (
            self.backoff_max_seconds < self.backoff_base_seconds
            or self.restricted_max_seconds < self.restricted_base_seconds
        ):
            raise ValueError("backoff maximum must not be below its base")

    @classmethod
    def from_ttls(cls, ttls: CacheTTLs) -> "FetchPolicy":
        return cls(ttl_seconds=ttls.fundamentals_seconds)


class FetchGate:
    """Process-wide serialization of provider calls, pause and restriction state.

    Streamlit sessions share a process, so all loaders use one gate: only one
    retrieval runs at a time, a second session finds the fresh cache afterwards,
    and a restriction seen by one session stops every session.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.last_call_at: datetime | None = None
        self.restricted_until: datetime | None = None


DEFAULT_GATE = FetchGate()


def installed_package_version() -> str:
    try:
        return importlib.metadata.version("yfinance")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


@dataclass(frozen=True, slots=True, repr=False)
class FundamentalsEntry:
    """What the loader knows about one instrument; values stay out of ``repr``."""

    status: FundamentalsStatus
    identity: VerifiedFundamentalIdentity | None = None
    normalized: NormalizedFundamentals | None = None
    report_set: FundamentalReportSet | None = None
    # When this exact content was first delivered; unchanged by a repeated
    # retrieval of the same content, so a manual input version stays valid.
    retrieved_at: datetime | None = None
    last_checked_at: datetime | None = None  # Latest successful retrieval.
    last_attempt_at: datetime | None = None  # Latest failed attempt, if any.
    failures: tuple[str, ...] = ()  # Sanitized codes of that failed attempt.
    notes: tuple[str, ...] = ()
    retry_after: datetime | None = None
    package_version: str | None = None

    @property
    def stale(self) -> bool:
        return bool(_STALE_NOTES & set(self.notes))

    def retrieval_age(self, now: datetime) -> timedelta | None:
        """Age of the last successful retrieval, not of the business reports."""
        if self.last_checked_at is None:
            return None
        return max(timedelta(0), now - self.last_checked_at)

    @property
    def latest_period_end(self) -> date | None:
        """End of the newest delivered fiscal year; None stays unknown, never now."""
        if self.normalized is None:
            return None
        ends = [
            o.period.end
            for o in self.normalized.observations
            if isinstance(o.period, FlowPeriod) and o.status is ValueStatus.AVAILABLE
        ]
        return max(ends, default=None)

    @property
    def bridge_date(self) -> date | None:
        return None if self.normalized is None else self.normalized.bridge_date

    def issues_at(self, valuation_at: datetime) -> tuple[QualityIssue, ...]:
        """Structural and age gate of the report set at the valuation time.

        A set that could not be built (withheld numbers, no annual period or share
        count) is reported by the normalization issues instead.
        """
        if self.report_set is not None:
            return self.report_set.issues_at(valuation_at)
        return () if self.normalized is None else self.normalized.issues


@dataclass(frozen=True, slots=True, repr=False)
class FundamentalsBundle:
    entries: Mapping[Instrument, FundamentalsEntry]
    loaded_at: datetime
    provider_calls: int = 0

    def entry(self, instrument: Instrument) -> FundamentalsEntry:
        return self.entries[instrument]


class FundamentalsLoader:
    """Cache-first loader; construction performs no IO and no provider access."""

    def __init__(
        self,
        subjects: FundamentalSubjects,
        provider: "FundamentalsProvider",
        store: FundamentalsStore,
        policy: FetchPolicy = FetchPolicy(),
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        package_version: str | None = None,
        gate: FetchGate | None = None,
    ) -> None:
        self._subjects = subjects
        self._provider = provider
        self._store = store
        self._policy = policy
        self._clock = clock
        self._sleep = sleep
        self._package_version = package_version or installed_package_version()
        self._gate = DEFAULT_GATE if gate is None else gate

    # --- public ---------------------------------------------------------------

    def load(
        self, instruments: Iterable[Instrument], *, refresh: bool = False
    ) -> FundamentalsBundle:
        """Return one entry per instrument; fetch only what is due and allowed.

        Raises ``PrivateStorageError`` (fixed messages) when access or the
        protected cache is unavailable; nothing is retrieved in that case.
        """
        self._store.require_access()
        wanted = list(dict.fromkeys(instruments))
        subject_of = {item: self._subjects.resolve(item) for item in wanted}
        keys: dict[tuple, FundamentalSubject] = {}
        for subject in subject_of.values():
            if subject is not None and not subject.identity.issues:
                keys.setdefault(subject.fetch_key, subject)
        records = {fk: self._store.get(store_key(fk)) or CacheRecord() for fk in keys}
        now = self._clock()
        due = []
        notes: dict[tuple, tuple[str, ...]] = {}
        for fk, record in records.items():
            is_due, why = self._due(record, now, refresh)
            notes[fk] = why
            if is_due:
                due.append(fk)
        # Never-loaded securities first, then the oldest retrieval.
        due.sort(
            key=lambda fk: (
                records[fk].success is not None,
                records[fk].success.checked_at if records[fk].success else now,
            )
        )
        calls = 0
        for fk in due:
            if calls >= self._policy.max_fetches_per_load:
                notes[fk] = (NOTE_DEFERRED,)
                continue
            records[fk], notes[fk], called = self._retrieve(keys[fk], refresh)
            calls += called
        now = self._clock()
        entries = {}
        for item, subject in subject_of.items():
            if subject is None:
                entries[item] = FundamentalsEntry(FundamentalsStatus.UNMAPPED)
            elif subject.identity.issues:
                entries[item] = FundamentalsEntry(
                    FundamentalsStatus.UNSUPPORTED,
                    subject.identity,
                    notes=tuple(i.code for i in subject.identity.issues),
                )
            else:
                entries[item] = self._entry(
                    subject, records[subject.fetch_key], now, notes[subject.fetch_key]
                )
        return FundamentalsBundle(entries, now, calls)

    # --- decisions ------------------------------------------------------------

    def _fresh(self, success: StoredFetch | None, now: datetime) -> bool:
        return (
            success is not None
            and success.package_version == self._package_version
            and timedelta(0) <= now - success.checked_at
            and now - success.checked_at < timedelta(seconds=self._policy.ttl_seconds)
        )

    def _due(
        self, record: CacheRecord, now: datetime, refresh: bool
    ) -> tuple[bool, tuple[str, ...]]:
        success, attempt = record.success, record.attempt
        fresh = self._fresh(success, now)
        if success is not None and refresh and fresh:
            age = now - success.checked_at
            if age < timedelta(seconds=self._policy.min_refetch_seconds):
                return False, (NOTE_TOO_SOON,)
        if fresh and not refresh:
            return False, ()
        if attempt is not None and attempt.retry_after and now < attempt.retry_after:
            return False, (NOTE_BACKOFF,)
        return True, ()

    def _delay(self, consecutive: int, restricted: bool) -> timedelta:
        base, cap = (
            (self._policy.restricted_base_seconds, self._policy.restricted_max_seconds)
            if restricted
            else (self._policy.backoff_base_seconds, self._policy.backoff_max_seconds)
        )
        return timedelta(seconds=min(base * 2 ** min(consecutive - 1, 32), cap))

    # --- retrieval ------------------------------------------------------------

    def _retrieve(
        self, subject: FundamentalSubject, refresh: bool
    ) -> tuple[CacheRecord, tuple[str, ...], int]:
        """One serialized retrieval; returns the record, notes and call count (0/1)."""
        key = store_key(subject.fetch_key)
        with self._gate.lock:
            # Access is rechecked right before the provider; another session may
            # also have fetched meanwhile, so the cache is read again.
            self._store.require_access()
            record = self._store.get(key) or CacheRecord()
            now = self._clock()
            is_due, why = self._due(record, now, refresh)
            if not is_due:
                return record, why, 0
            restricted_until = self._gate.restricted_until
            if restricted_until is not None and now < restricted_until:
                return record, (NOTE_PROVIDER_BACKOFF,), 0
            self._pause(now)
            try:
                statements = self._provider.fetch(subject.identity)
            except Exception:  # noqa: BLE001 - the adapter never raises; a foreign one may
                statements = None  # Neither message nor type is kept.
            done = self._clock()
            self._gate.last_call_at = done
            codes = (
                ("request_provider_error",)
                if statements is None
                else self._failure_codes(subject, statements)
            )
            if codes:
                restricted = any(c.endswith(FAILURE_ACCESS_RESTRICTED) for c in codes)
                consecutive = 1 + (record.attempt.consecutive if record.attempt else 0)
                attempt = FailedAttempt(
                    done,
                    codes,
                    consecutive,
                    done + self._delay(consecutive, restricted),
                )
                self._store.save_failure(key, attempt)
                if restricted:
                    self._gate.restricted_until = attempt.retry_after
                return CacheRecord(record.success, attempt), (), 1
            success = self._success(record.success, statements, done)
            self._store.save_success(key, success)
            unchanged = (
                record.success is not None
                and success.statements is record.success.statements
            )
            return CacheRecord(success, None), (NOTE_UNCHANGED,) if unchanged else (), 1

    def _pause(self, now: datetime) -> None:
        last = self._gate.last_call_at
        if last is None:
            return
        wait = self._policy.pause_seconds - (now - last).total_seconds()
        if wait > 0:
            self._sleep(wait)

    def _failure_codes(
        self, subject: FundamentalSubject, statements: ProviderStatements
    ) -> tuple[str, ...]:
        """Sanitized codes of an unusable retrieval; empty means complete and consistent."""
        codes = [f"{f.dataset}_{f.code}" for f in statements.failures]
        if not codes:
            # Reported failures already explain missing parts (an aborted request
            # leaves all of them empty); only an unexplained gap is added.
            for part, dataset in _DATASET_FOR_PART:
                if getattr(statements, part) is None:
                    codes.append(f"{dataset}_empty")
        if not codes:
            normalized = normalize_statements(
                subject.identity, statements, subject.expectation
            )
            if normalized.numbers_withheld:
                # The new answer contradicts the owner's expectations: keep the
                # previous state rather than replacing it with withheld numbers.
                codes = ["numbers_withheld", *(i.code for i in normalized.issues)]
        return tuple(
            dict.fromkeys(sorted(c for c in codes if CODE_PATTERN.fullmatch(c)))
        )

    def _success(
        self, prior: StoredFetch | None, statements: ProviderStatements, done: datetime
    ) -> StoredFetch:
        if prior is not None and (
            replace(statements, retrieved_at=prior.statements.retrieved_at)
            == prior.statements
        ):
            # Same content: it keeps its first delivery time, so report set and
            # input version stay stable; only the check time moves on.
            statements = prior.statements
        return StoredFetch(statements, self._package_version, done)

    # --- result ---------------------------------------------------------------

    def _entry(
        self,
        subject: FundamentalSubject,
        record: CacheRecord,
        now: datetime,
        extra: tuple[str, ...],
    ) -> FundamentalsEntry:
        success, attempt = record.success, record.attempt
        failures = () if attempt is None else attempt.codes
        common = {
            "identity": subject.identity,
            "last_attempt_at": None if attempt is None else attempt.at,
            "failures": failures,
            "retry_after": None if attempt is None else attempt.retry_after,
        }
        if success is None:
            return FundamentalsEntry(
                FundamentalsStatus.UNAVAILABLE, notes=extra, **common
            )
        notes = list(extra)
        if success.package_version != self._package_version:
            notes.append(NOTE_PACKAGE_CHANGED)
        if not (
            timedelta(0) <= now - success.checked_at
            and now - success.checked_at < timedelta(seconds=self._policy.ttl_seconds)
        ):
            notes.append(NOTE_WINDOW_EXPIRED)
        if attempt is not None:
            notes.append(NOTE_REFRESH_FAILED)
        normalized = normalize_statements(
            subject.identity, success.statements, subject.expectation
        )
        report_set = None
        if normalized.shares_period is not None:
            try:
                report_set = build_report_set(
                    normalized,
                    subject.capital_structure(normalized.shares_period.as_of),
                    f"{normalized.report.report_set_id}/{subject.owner_digest()}",
                    subject.confirmations,
                )
            except (TypeError, ValueError):
                notes.append(NOTE_REPORT_SET_REJECTED)
        stale = bool(_STALE_NOTES & set(notes))
        return FundamentalsEntry(
            FundamentalsStatus.CACHED if stale else FundamentalsStatus.CURRENT,
            normalized=normalized,
            report_set=report_set,
            retrieved_at=success.statements.retrieved_at,
            last_checked_at=success.checked_at,
            notes=tuple(dict.fromkeys(notes)),
            package_version=success.package_version,
            **common,
        )


# --- construction from private runtime configuration ---------------------------


def _loader(config: AppConfig, subjects: FundamentalSubjects) -> FundamentalsLoader:
    # Imported here: the module stays free of pandas/yfinance until a loader exists.
    from portfolio_dashboard.fundamentals_provider import YFinanceFundamentalsProvider
    from portfolio_dashboard.private_store import PrivateDatabase

    return FundamentalsLoader(
        subjects,
        YFinanceFundamentalsProvider(),
        PrivateFundamentalsStore(PrivateDatabase()),
        FetchPolicy.from_ttls(config.ttls),
    )


def build_local_fundamentals_loader(config: AppConfig) -> FundamentalsLoader | None:
    """Local mode: identities from ``PORTFOLIO_FUNDAMENTALS_FILE``; none, no loader."""
    path = config.source.fundamentals_file
    if not path:
        return None
    return _loader(config, load_fundamental_subjects(path))


def build_production_fundamentals_loader(
    config: AppConfig, entries: object
) -> FundamentalsLoader | None:
    """Production: ``[[fundamental]]`` from the host secrets; none, no loader."""
    if not entries:
        return None
    return _loader(config, parse_fundamental_subjects(entries))
