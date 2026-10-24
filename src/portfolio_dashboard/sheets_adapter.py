"""One bounded, read-only Sheets REST request per explicit import attempt.

No environment/credential-file reads, discovery, retries, cache or UI wiring.
The host must authorize the owner before constructing a session or calling read.
"""

import logging
import math
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from urllib.parse import quote

from requests.exceptions import Timeout

from portfolio_dashboard.config import SourceConfig, SourceKind
from portfolio_dashboard.models import DataWarning, PortfolioSnapshot, SnapshotStatus
from portfolio_dashboard.sheet_parser import (
    SheetMetadata,
    SheetParseResult,
    parse_sheet_rows,
)

SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
_RANGE = re.compile(r"(?:'(?:[^']|'')+'|[^'!\r\n]+)!A([1-9][0-9]*):([M-T])([1-9][0-9]*)")
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


@dataclass(frozen=True, slots=True)
class SheetsAdapter:
    source: SourceConfig = field(repr=False)
    metadata: SheetMetadata = field(repr=False)
    session: SheetsSession = field(repr=False)
    timeout_seconds: float = 10
    _url: str = field(init=False, repr=False)
    _header_row: int = field(init=False, repr=False)
    _row_count: int = field(init=False, repr=False)
    _column_count: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source, SourceConfig)
            or self.source.kind is not SourceKind.GOOGLE_SHEETS
        ):
            raise SheetsConfigurationError("Explicit Google Sheets source required.")
        source_id = self.source.spreadsheet_id
        if not isinstance(source_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", source_id
        ):
            raise SheetsConfigurationError(
                "Spreadsheet identifier is missing or invalid."
            )
        cell_range = self.source.worksheet_range
        match = _RANGE.fullmatch(cell_range) if isinstance(cell_range, str) else None
        if match is None:
            raise SheetsConfigurationError(
                "Explicit worksheet and bounded A–M..A–T row range required."
            )
        try:
            first, last = int(match.group(1)), int(match.group(3))
        except ValueError:
            raise SheetsConfigurationError("Invalid bounded row range.") from None
        if last < first:
            raise SheetsConfigurationError("Invalid bounded row range.")
        if not isinstance(self.metadata, SheetMetadata):
            raise SheetsConfigurationError("Explicit Sheet metadata required.")
        object.__setattr__(self, "timeout_seconds", _timeout(self.timeout_seconds))
        object.__setattr__(self, "_header_row", first)
        object.__setattr__(self, "_row_count", last - first + 1)
        object.__setattr__(self, "_column_count", ord(match.group(2)) - ord("A") + 1)
        object.__setattr__(
            self,
            "_url",
            "https://sheets.googleapis.com/v4/spreadsheets/"
            f"{quote(source_id, safe='')}/values/{quote(cell_range, safe='')}",
        )

    def read(self, *, attempted_at: datetime) -> SheetsReadResult:
        """Fetch once, atomically validate the envelope, then reuse the row parser.

        The supplied aware timestamp describes this attempt/import, never a price
        observation. Failures contain no holdings and never reuse prior results.
        """
        # Validate before any I/O, including for empty or failed responses.
        PortfolioSnapshot(attempted_at, SnapshotStatus.COMPLETE)
        try:
            with _private_transport_diagnostics():
                response = self.session.get(
                    self._url,
                    params={
                        "majorDimension": "ROWS",
                        "valueRenderOption": "UNFORMATTED_VALUE",
                        "dateTimeRenderOption": "SERIAL_NUMBER",
                        "fields": "majorDimension,values",
                    },
                    timeout=self.timeout_seconds,
                    allow_redirects=False,
                    verify=True,
                )
                try:
                    status = response.status_code
                    if type(status) is not int:
                        return self._invalid_response(attempted_at)
                    if status != 200:
                        return _http_failure(attempted_at, status)
                    payload = response.json(
                        parse_float=Decimal, parse_constant=_reject_constant
                    )
                finally:
                    response.close()
        except (Timeout, TimeoutError):
            return _failed(attempted_at, "sheet_timeout", "Sheet read timed out.")
        except ValueError:
            return self._invalid_response(attempted_at)
        except Exception:
            # Transport/auth exceptions can contain URLs, response bodies or tokens.
            return _failed(
                attempted_at,
                "sheet_transport_error",
                "Sheet read could not be completed.",
            )

        if not isinstance(payload, dict) or payload.get("majorDimension") != "ROWS":
            return self._invalid_response(attempted_at)
        rows = payload.get("values", [])
        if not isinstance(rows, list) or len(rows) > self._row_count:
            return self._invalid_response(attempted_at)
        if any(
            not isinstance(row, list)
            or len(row) > self._column_count
            or any(
                type(cell) not in (str, int, float, Decimal, bool, type(None))
                for cell in row
            )
            for row in rows
        ):
            return self._invalid_response(attempted_at)
        if not rows:
            return SheetsReadResult(
                SheetParseResult(
                    PortfolioSnapshot(attempted_at, SnapshotStatus.COMPLETE)
                )
            )
        return SheetsReadResult(
            parse_sheet_rows(
                rows[0],
                rows[1:],
                imported_at=attempted_at,
                metadata=self.metadata,
                header_row=self._header_row,
            )
        )

    @staticmethod
    def _invalid_response(attempted_at: datetime) -> SheetsReadResult:
        return _failed(
            attempted_at,
            "sheet_invalid_response",
            "Sheet response could not be interpreted.",
        )
