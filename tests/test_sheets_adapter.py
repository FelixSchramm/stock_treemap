"""Synthetic REST/session responses only; pytest blocks all IP sockets by default."""

import json
import logging
import traceback
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from requests import Response, Session
from requests.adapters import BaseAdapter
from requests.exceptions import ConnectionError, Timeout

from portfolio_dashboard.config import SourceConfig, SourceKind
from portfolio_dashboard.models import SnapshotStatus, ValuationSource
from portfolio_dashboard.sheet_parser import SHEET_HEADERS, NumberLocale, SheetMetadata
from portfolio_dashboard.sheets_adapter import (
    SheetsAdapter,
    SheetsConfigurationError,
)

ATTEMPT = datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc)
SOURCE = SourceConfig(
    SourceKind.GOOGLE_SHEETS,
    "synthetic-private-source",
    "'Synthetic private tab'!A7:M20",
    "/nonexistent/synthetic-private-credentials.json",
)
METADATA = SheetMetadata(NumberLocale.GERMAN)
PRIVATE = "synthetic-private-row Authorization: Bearer synthetic-private-token"


def row(quantity="1,125"):
    return [
        "Synthetic holding",
        "DEMO",
        "ETF",
        quantity,
        "DEMO-VENUE:DEMO",
        "USD",
        "12,50",
        "14,0625",
        "10,00",
        "40,625",
        "4,0625",
        "Synthetic",
        "25",
    ]


class SyntheticResponse:
    def __init__(self, payload=None, *, status=200, raw=None):
        self.status_code = status
        self.body = json.dumps(payload) if raw is None else raw
        self.decoded = False
        self.closed = False

    def json(self, **kwargs):
        self.decoded = True
        return json.loads(self.body, **kwargs)

    def close(self):
        self.closed = True


class SyntheticSession:
    """The adapter cannot succeed if it tries any method other than GET."""

    def __init__(self, *events):
        self.events = iter(events)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        event = next(self.events)
        if isinstance(event, Exception):
            raise event
        return event

    def __repr__(self):
        return PRIVATE


def response(rows):
    return SyntheticResponse({"majorDimension": "ROWS", "values": rows})


def adapter(*events, **kwargs):
    session = SyntheticSession(*events)
    return SheetsAdapter(SOURCE, METADATA, session, **kwargs), session


def test_one_explicit_bounded_get_parses_rows_and_preserves_provenance():
    received = response([list(SHEET_HEADERS), row()])
    reader, session = adapter(received, timeout_seconds=3.5)
    result = reader.read(attempted_at=ATTEMPT)
    assert result.snapshot.status is SnapshotStatus.COMPLETE
    assert result.snapshot.attempted_at == ATTEMPT
    (position,) = result.snapshot.positions
    assert position.source_row == 8
    assert position.imported_at == ATTEMPT
    assert position.quantity == Decimal("1.125")
    assert position.valuation.price == Decimal("12.50")
    assert position.valuation.provenance.source is ValuationSource.SHEET
    assert position.valuation.provenance.retrieved_at == ATTEMPT
    assert position.valuation.provenance.observed_at is None
    assert not position.cost_basis.usable_for_since_purchase
    assert result.parsed.references[0].gain_loss_percent_points == Decimal("40.625")
    assert received.closed
    assert session.calls == [
        (
            "https://sheets.googleapis.com/v4/spreadsheets/synthetic-private-source/values/"
            "%27Synthetic%20private%20tab%27%21A7%3AM20",
            {
                "params": {
                    "majorDimension": "ROWS",
                    "valueRenderOption": "UNFORMATTED_VALUE",
                    "dateTimeRenderOption": "SERIAL_NUMBER",
                    "fields": "majorDimension,values",
                },
                "timeout": 3.5,
                "allow_redirects": False,
                "verify": True,
            },
        )
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"majorDimension": "ROWS"},
        {"majorDimension": "ROWS", "values": []},
        {"majorDimension": "ROWS", "values": [list(SHEET_HEADERS)]},
    ],
)
def test_empty_source_or_header_only_is_success(payload):
    reader, _ = adapter(SyntheticResponse(payload))
    result = reader.read(attempted_at=ATTEMPT)
    assert result.snapshot.status is SnapshotStatus.COMPLETE
    assert result.snapshot.positions == ()
    assert result.snapshot.warnings == ()


def test_partial_import_and_schema_failure_are_not_complete_holdings():
    bad_headers = list(SHEET_HEADERS)
    bad_headers[0] = PRIVATE
    reader, _ = adapter(
        response([list(SHEET_HEADERS), row(), row("bad"), row("-2"), row("0")]),
        response([bad_headers, row()]),
    )
    partial = reader.read(attempted_at=ATTEMPT)
    assert partial.snapshot.status is SnapshotStatus.PARTIAL
    assert len(partial.parsed.active_positions) == 1
    assert partial.parsed.negative_positions[0].quantity == Decimal("-2")
    assert {item.reason for item in partial.parsed.excluded_rows} == {
        "invalid_position",
        "zero_quantity",
    }
    schema = reader.read(attempted_at=ATTEMPT + timedelta(minutes=1))
    assert schema.snapshot.status is SnapshotStatus.FAILED
    assert not schema.snapshot.positions
    assert schema.snapshot.warnings[0].code == "missing_column"
    assert schema.snapshot.warnings[0].source_row == 7
    assert PRIVATE not in repr(schema.snapshot.warnings)


def test_each_refresh_is_stateless_with_new_time_and_no_invented_last_good():
    reader, session = adapter(
        response([list(SHEET_HEADERS), row()]),
        Timeout(PRIVATE),
        response([list(SHEET_HEADERS), row("2")]),
    )
    first = reader.read(attempted_at=ATTEMPT)
    second = reader.read(attempted_at=ATTEMPT + timedelta(minutes=1))
    third = reader.read(attempted_at=ATTEMPT + timedelta(minutes=2))
    assert first.snapshot.positions[0].quantity == Decimal("1.125")
    assert second.snapshot.status is SnapshotStatus.FAILED
    assert not second.snapshot.positions
    assert second.snapshot.attempted_at == ATTEMPT + timedelta(minutes=1)
    assert third.snapshot.positions[0].quantity == Decimal("2")
    assert third.snapshot.positions[
        0
    ].valuation.provenance.retrieved_at == ATTEMPT + timedelta(minutes=2)
    assert third.snapshot.positions[0].valuation.provenance.observed_at is None
    assert len(session.calls) == 3
    restarted, _ = adapter(Timeout(PRIVATE))
    assert not restarted.read(attempted_at=ATTEMPT).snapshot.positions


def test_explicit_price_time_stays_separate_from_import_time_and_json_decimals():
    observed = ATTEMPT - timedelta(days=2)
    received = SyntheticResponse(
        raw='{"majorDimension":"ROWS","values":['
        + json.dumps(list(SHEET_HEADERS))
        + ',["Synthetic holding","DEMO","ETF",0.123456789123456789,""]]}'
    )
    reader = SheetsAdapter(
        SOURCE,
        replace(METADATA, sheet_price_observed_at=observed),
        SyntheticSession(received),
    )
    position = reader.read(attempted_at=ATTEMPT).snapshot.positions[0]
    assert position.quantity == Decimal("0.123456789123456789")
    assert position.valuation.provenance.observed_at == observed
    assert position.valuation.provenance.retrieved_at == ATTEMPT


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "sheet_authentication_failed"),
        (403, "sheet_permission_denied"),
        (404, "sheet_source_unavailable"),
        (429, "sheet_rate_limited"),
        (503, "sheet_unavailable"),
        (302, "sheet_http_error"),
        (400, "sheet_http_error"),
    ],
)
def test_http_errors_do_not_decode_or_expose_provider_bodies(
    status, code, caplog, capsys
):
    received = SyntheticResponse(status=status, raw=PRIVATE)
    reader, session = adapter(received)
    result = reader.read(attempted_at=ATTEMPT)
    assert result.snapshot.status is SnapshotStatus.FAILED
    assert result.snapshot.positions == ()
    assert result.snapshot.warnings[0].code == code
    assert received.closed and not received.decoded
    assert len(session.calls) == 1
    assert PRIVATE not in repr(result.snapshot.warnings)
    assert not caplog.records
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (Timeout(PRIVATE), "sheet_timeout"),
        (TimeoutError(PRIVATE), "sheet_timeout"),
        (ConnectionError(PRIVATE), "sheet_transport_error"),
        (RuntimeError(PRIVATE), "sheet_transport_error"),
    ],
)
def test_transport_and_auth_errors_are_neutral_and_not_retried(
    error, code, caplog, capsys
):
    reader, session = adapter(error)
    result = reader.read(attempted_at=ATTEMPT)
    assert result.snapshot.status is SnapshotStatus.FAILED
    assert result.snapshot.warnings[0].code == code
    assert not result.snapshot.positions
    assert len(session.calls) == 1
    assert PRIVATE not in repr(result.snapshot.warnings)
    assert not caplog.records
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"majorDimension": "COLUMNS", "values": []},
        {"majorDimension": "ROWS", "values": None},
        {"majorDimension": "ROWS", "values": [PRIVATE]},
        {"majorDimension": "ROWS", "values": [[{"raw": PRIVATE}]]},
        {"majorDimension": "ROWS", "values": [[0] * 14]},
        {"majorDimension": "ROWS", "values": [[]] * 15},
    ],
)
def test_malformed_or_out_of_bounds_envelopes_fail_atomically(payload):
    received = SyntheticResponse(payload)
    reader, _ = adapter(received)
    result = reader.read(attempted_at=ATTEMPT)
    assert result.snapshot.status is SnapshotStatus.FAILED
    assert result.snapshot.warnings[0].code == "sheet_invalid_response"
    assert not result.snapshot.positions
    assert received.closed


@pytest.mark.parametrize("raw", [PRIVATE, '{"majorDimension":"ROWS","values":[[NaN]]}'])
def test_invalid_json_is_not_a_successful_empty_import(raw):
    reader, _ = adapter(SyntheticResponse(raw=raw))
    result = reader.read(attempted_at=ATTEMPT)
    assert result.snapshot.status is SnapshotStatus.FAILED
    assert result.snapshot.warnings[0].code == "sheet_invalid_response"
    assert PRIVATE not in repr(result.snapshot.warnings)


@pytest.mark.parametrize(
    "source",
    [
        SourceConfig(),
        replace(SOURCE, spreadsheet_id=None),
        replace(SOURCE, spreadsheet_id="https://example.invalid/" + PRIVATE),
        replace(SOURCE, worksheet_range=None),
        *[
            replace(SOURCE, worksheet_range=value)
            for value in (
                "A7:M20",
                "Synthetic!A:M",
                "Synthetic!A7:M",
                "Synthetic!A7:M6",
                "Synthetic!B7:M20",
                "Synthetic!A7:U20",
                "Synthetic!A0:M20",
                PRIVATE,
            )
        ],
    ],
)
def test_source_validation_is_bounded_explicit_and_neutral(source):
    session = SyntheticSession()
    with pytest.raises(SheetsConfigurationError) as caught:
        SheetsAdapter(source, METADATA, session)
    assert PRIVATE not in "".join(traceback.format_exception(caught.value))
    assert session.calls == []


@pytest.mark.parametrize("timeout", [0, -1, True, "bad", float("nan"), float("inf")])
def test_invalid_timeout_fails_before_transport(timeout):
    with pytest.raises(SheetsConfigurationError):
        adapter(timeout_seconds=timeout)


def test_aware_timestamp_is_required_before_any_request():
    reader, session = adapter()
    with pytest.raises(ValueError, match="timezone"):
        reader.read(attempted_at=ATTEMPT.replace(tzinfo=None))
    assert session.calls == []


def test_adapter_and_outer_result_reprs_hide_source_session_and_holdings():
    holding = row()
    holding[0] = PRIVATE
    reader, _ = adapter(response([list(SHEET_HEADERS), holding]))
    result = reader.read(attempted_at=ATTEMPT)
    for item in (reader, result):
        for private in (
            SOURCE.spreadsheet_id,
            SOURCE.worksheet_range,
            SOURCE.credentials_file,
            PRIVATE,
        ):
            assert private not in str(item)
            assert private not in repr(item)
    assert "complete" in repr(result)


def test_http_diagnostics_cannot_expose_private_reads_at_debug(caplog):
    class ChattySession(SyntheticSession):
        def get(self, url, **kwargs):
            logger = logging.getLogger("urllib3.connectionpool")
            logger.debug("GET %s %s", url, PRIVATE, extra={"httpResponse": PRIVATE})
            logger.warning("Provider failure %s", PRIVATE)
            return super().get(url, **kwargs)

    session = ChattySession(response([list(SHEET_HEADERS), row()]))
    reader = SheetsAdapter(SOURCE, METADATA, session)
    with caplog.at_level(logging.DEBUG):
        assert (
            reader.read(attempted_at=ATTEMPT).snapshot.status is SnapshotStatus.COMPLETE
        )
        logging.getLogger("urllib3.connectionpool").debug(
            "Unrelated diagnostics remain available"
        )
    assert [record.getMessage() for record in caplog.records] == [
        "Unrelated diagnostics remain available"
    ]
    assert PRIVATE not in caplog.text
    assert SOURCE.spreadsheet_id not in caplog.text


@pytest.mark.parametrize("status", [200, 401, 403])
def test_requests_session_uses_only_one_synthetic_get(status, caplog):
    class MemoryTransport(BaseAdapter):
        def __init__(self):
            self.requests = []

        def send(self, request, **kwargs):
            self.requests.append(request)
            assert kwargs["timeout"] == 2
            # requests may resolve True to the environment's trusted CA bundle.
            assert kwargs["verify"]
            result = Response()
            result.status_code = status
            result.request = request
            result.url = request.url
            result._content = (
                json.dumps(
                    {
                        "majorDimension": "ROWS",
                        "values": [list(SHEET_HEADERS), row()],
                    }
                ).encode()
                if status == 200
                else PRIVATE.encode()
            )
            result._content_consumed = True
            return result

        def close(self):
            pass

    transport = MemoryTransport()
    with Session() as session:
        # Synthetic header tests REST dispatch only, not real token authorization.
        session.headers["Authorization"] = "Bearer synthetic-access-token"
        session.mount("https://", transport)
        reader = SheetsAdapter(SOURCE, METADATA, session, timeout_seconds=2)
        with caplog.at_level(logging.DEBUG):
            result = reader.read(attempted_at=ATTEMPT)
    assert result.snapshot.status is (
        SnapshotStatus.COMPLETE if status == 200 else SnapshotStatus.FAILED
    )
    if status != 200:
        assert result.snapshot.warnings[0].code == (
            "sheet_authentication_failed"
            if status == 401
            else "sheet_permission_denied"
        )
    assert len(transport.requests) == 1
    (request,) = transport.requests
    assert request.method == "GET"
    assert request.body is None
    assert request.headers["Authorization"] == "Bearer synthetic-access-token"
    assert request.url.startswith("https://sheets.googleapis.com/v4/spreadsheets/")
    assert not caplog.records


def test_range_up_to_column_t_reads_optional_hint_columns():
    source = replace(SOURCE, worksheet_range="'Synthetic private tab'!A1:T30")
    hint_headers = list(SHEET_HEADERS) + [""] * 4 + ["ISIN", "Handelsplatz", "Kursdatum"]
    data = row() + [""] * 4 + ["DE0000000001", "Synthetic venue", 46027]
    reader = SheetsAdapter(source, METADATA, SyntheticSession(response([hint_headers, data])))
    result = reader.read(attempted_at=ATTEMPT)
    (position,) = result.snapshot.positions
    assert position.instrument.source_isin == "DE0000000001"
    assert position.valuation.provenance.observed_at.date().isoformat() == "2026-01-05"


def test_rows_wider_than_the_configured_end_column_fail_atomically():
    wide = row() + ["x"]
    reader, _ = adapter(response([list(SHEET_HEADERS), wide]))
    result = reader.read(attempted_at=ATTEMPT)
    assert not result.snapshot.positions
