"""Host-side construction of the read-only Sheets session.

Local mode reads the key file named by PORTFOLIO_SHEETS_CREDENTIALS_FILE;
production passes the server-side [sheets_service_account] secret section.
Key content, parse errors and the path never appear in raised messages.
"""

import json
from collections.abc import Mapping
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from portfolio_dashboard.sheets_adapter import SHEETS_READONLY_SCOPE


class SheetsCredentialError(ValueError):
    pass


def _credential_error() -> SheetsCredentialError:
    return SheetsCredentialError("Service-Account-Schlüssel fehlt oder ist ungültig.")


def build_readonly_session(credentials_file: str) -> AuthorizedSession:
    """Authorize a session from a key file, limited to the readonly scope."""
    try:
        info = json.loads(Path(credentials_file).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - never expose key material or its path
        raise _credential_error() from None
    return build_readonly_session_from_info(info)


def build_readonly_session_from_info(info: object) -> AuthorizedSession:
    """Authorize a session from key fields, limited to the readonly scope."""
    try:
        if not isinstance(info, Mapping) or info.get("type") != "service_account":
            raise ValueError
        credentials = service_account.Credentials.from_service_account_info(
            dict(info), scopes=[SHEETS_READONLY_SCOPE]
        )
    except Exception:  # noqa: BLE001 - never expose key material
        raise _credential_error() from None
    return AuthorizedSession(credentials)
