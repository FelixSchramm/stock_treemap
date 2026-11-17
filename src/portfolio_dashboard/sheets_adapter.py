"""One bounded, read-only Sheets REST request per explicit import attempt.

No environment/credential-file reads, discovery, retries, cache or UI wiring.
The host must authorize the owner before constructing a session or calling read.
The depot reads the journal tables of ADR 0012 in one ``values:batchGet``.
"""

import logging
import math
import re
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from urllib.parse import quote

from requests.exceptions import Timeout

from portfolio_dashboard.config import SourceConfig, SourceKind
from portfolio_dashboard.instruments import VerifiedListing
from portfolio_dashboard.models import DataWarning, PortfolioSnapshot, SnapshotStatus
from portfolio_dashboard.sheet_parser import (
    SheetParseResult,
)
from portfolio_dashboard.sheet_tables import (
    TABLE_RANGES,
    TableSchemaError,
    build_portfolio,
    parse_tables,
)

SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
_PRIVATE_READ = ContextVar("private_sheet_read", default=False)


class _TransportLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _PRIVATE_READ.get()


_TRANSPORT_LOG_FILTER = _TransportLogFilter()


@contextmanager
def _private_transport_diagnostics():
    # HTTP DEBUG records can contain full URLs or provider details.
    # Filters remain inert outside this context and do not silence other threads.
    logging.getLogger("urllib3.connectionpool").addFilter(_TRANSPORT_LOG_FILTER)
    token = _PRIVATE_READ.set(True)
    try:
        yield
    finally:
        _PRIVATE_READ.reset(token)


class SheetsConfigurationError(ValueError):
    """Neutral input error; never includes source or credential values."""


class SheetsResponse(Protocol):
    status_code: int

    def json(self, **kwargs: object) -> object: ...

    def close(self) -> None: ...


class SheetsSession(Protocol):
    """Host-authorized transport with the sole spreadsheets.readonly scope.

    Scope/identity verification and credential lifecycle belong to the host.
    Only GET is available to this adapter, including in synthetic transports.
    """

    def get(
        self,
        url: str,
        *,
        params: dict[str, str],
        timeout: float,
        allow_redirects: bool,
        verify: bool,
    ) -> SheetsResponse: ...


def _timeout(value: float) -> float:
    if type(value) not in (int, float):
        raise SheetsConfigurationError("Timeout must be finite and positive.")
    try:
        valid = math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise SheetsConfigurationError("Timeout must be finite and positive.")
    return float(value)


@dataclass(frozen=True, slots=True, repr=False)
class SheetsReadResult:
    """Safe outer representation; parsed holdings still require protected access."""

    parsed: SheetParseResult

    @property
    def snapshot(self) -> PortfolioSnapshot:
        return self.parsed.snapshot

    def __repr__(self) -> str:
        return f"SheetsReadResult(status={self.snapshot.status.value!r})"


def _failed(attempted_at: datetime, code: str, message: str) -> SheetsReadResult:
    return SheetsReadResult(
        SheetParseResult(
            PortfolioSnapshot(
                attempted_at,
                SnapshotStatus.FAILED,
                warnings=(DataWarning(code, message),),
            )
        )
    )


def _http_failure(attempted_at: datetime, status: int) -> SheetsReadResult:
    if status == 401:
        code, message = (
            "sheet_authentication_failed",
            "Sheet identity was not accepted.",
        )
    elif status == 403:
        code, message = "sheet_permission_denied", "Sheet read permission was denied."
    elif status == 404:
        code, message = (
            "sheet_source_unavailable",
            "Configured Sheet source is unavailable.",
        )
    elif status == 429:
        code, message = "sheet_rate_limited", "Sheet read limit was reached."
    elif 500 <= status <= 599:
        code, message = "sheet_unavailable", "Sheet service is temporarily unavailable."
    else:
        code, message = "sheet_http_error", "Sheet read request did not succeed."
    return _failed(attempted_at, code, message)


def _reject_constant(value: str) -> None:
    raise ValueError("Invalid JSON number.")


def _failed_snapshot(attempted_at: datetime, code: str, message: str) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        attempted_at, SnapshotStatus.FAILED, warnings=(DataWarning(code, message),)
    )


@dataclass(frozen=True, slots=True, repr=False)
class JournalReadResult:
    """Snapshot plus the owner-verified listings read in the same request."""

    snapshot: PortfolioSnapshot
    listings: tuple[VerifiedListing, ...] = ()

    def __repr__(self) -> str:
        return f"JournalReadResult(status={self.snapshot.status.value!r})"


@dataclass(frozen=True, slots=True)
class JournalSheetsReader:
    """One read-only ``values:batchGet`` for Instrumente, Transaktionen, Kurse_manuell.

    Same transport rules as ``SheetsAdapter``: single GET, no redirects, TLS
    verification, neutral failures and private transport diagnostics.
    ``venues`` maps each supported Yahoo venue code to its required currency.
    """

    source: SourceConfig = field(repr=False)
    session: SheetsSession = field(repr=False)
    venues: Mapping[str, str] = field(repr=False)
    timeout_seconds: float = 10
    _url: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source, SourceConfig)
            or self.source.kind is not SourceKind.GOOGLE_SHEETS
        ):
            raise SheetsConfigurationError("Explicit Google Sheets source required.")
        source_id = self.source.spreadsheet_id
        if not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", source_id):
            raise SheetsConfigurationError("Spreadsheet identifier is missing or invalid.")
        object.__setattr__(self, "timeout_seconds", _timeout(self.timeout_seconds))
        query = "&".join(f"ranges={quote(r, safe='')}" for r in TABLE_RANGES)
        object.__setattr__(
            self,
            "_url",
            "https://sheets.googleapis.com/v4/spreadsheets/"
            f"{quote(source_id, safe='')}/values:batchGet?{query}",
        )

    def read(self, *, attempted_at: datetime) -> JournalReadResult:
        """Fetch once, validate the envelope, then parse and build the depot."""
        PortfolioSnapshot(attempted_at, SnapshotStatus.COMPLETE)  # validates the clock
        try:
            with _private_transport_diagnostics():
                response = self.session.get(
                    self._url,
                    params={
                        "majorDimension": "ROWS",
                        "valueRenderOption": "UNFORMATTED_VALUE",
                        "dateTimeRenderOption": "SERIAL_NUMBER",
                        "fields": "valueRanges(majorDimension,values)",
                    },
                    timeout=self.timeout_seconds,
                    allow_redirects=False,
                    verify=True,
                )
                try:
                    status = response.status_code
                    if type(status) is not int:
                        return self._invalid(attempted_at)
                    if status != 200:
                        return JournalReadResult(_http_failure(attempted_at, status).snapshot)
                    payload = response.json(parse_float=Decimal, parse_constant=_reject_constant)
                finally:
                    response.close()
        except (Timeout, TimeoutError):
            return JournalReadResult(
                _failed_snapshot(attempted_at, "sheet_timeout", "Sheet read timed out.")
            )
        except ValueError:
            return self._invalid(attempted_at)
        except Exception:
            # Transport/auth exceptions can contain URLs, response bodies or tokens.
            return JournalReadResult(_failed_snapshot(
                attempted_at, "sheet_transport_error", "Sheet read could not be completed."
            ))

        tables = payload.get("valueRanges") if isinstance(payload, dict) else None
        if not isinstance(tables, list) or len(tables) != len(TABLE_RANGES):
            return self._invalid(attempted_at)
        values = []
        for entry, cell_range in zip(tables, TABLE_RANGES):
            match = re.search(r"!A1:([A-Z])([0-9]+)$", cell_range)
            columns, rows_allowed = ord(match.group(1)) - ord("A") + 1, int(match.group(2))
            rows = entry.get("values", []) if isinstance(entry, dict) else None
            if (
                not isinstance(entry, dict)
                or entry.get("majorDimension", "ROWS") != "ROWS"
                or not isinstance(rows, list)
                or len(rows) > rows_allowed
                or any(
                    not isinstance(row, list)
                    or len(row) > columns
                    or any(
                        type(cell) not in (str, int, float, Decimal, bool, type(None))
                        for cell in row
                    )
                    for row in rows
                )
            ):
                return self._invalid(attempted_at)
            values.append(rows)
        try:
            parsed = parse_tables(*values)
        except TableSchemaError as exc:
            return JournalReadResult(
                _failed_snapshot(attempted_at, "sheet_schema_error", str(exc))
            )
        result = build_portfolio(parsed, imported_at=attempted_at, venues=self.venues)
        return JournalReadResult(result.snapshot, result.listings)

    @staticmethod
    def _invalid(attempted_at: datetime) -> JournalReadResult:
        return JournalReadResult(_failed_snapshot(
            attempted_at, "sheet_invalid_response", "Sheet response could not be interpreted."
        ))
