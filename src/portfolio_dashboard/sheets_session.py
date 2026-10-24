"""Host-side construction of the read-only Sheets session from a key file.

The path comes from PORTFOLIO_SHEETS_CREDENTIALS_FILE via SourceConfig. The
file content, its parse errors and the path never appear in raised messages.
"""

import json
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from portfolio_dashboard.sheets_adapter import SHEETS_READONLY_SCOPE


class SheetsCredentialError(ValueError):
    pass


def build_readonly_session(credentials_file: str) -> AuthorizedSession:
    """Authorize a session as the service account, limited to the readonly scope."""
    try:
        info = json.loads(Path(credentials_file).read_text(encoding="utf-8"))
        if not isinstance(info, dict) or info.get("type") != "service_account":
            raise ValueError
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=[SHEETS_READONLY_SCOPE]
        )
    except Exception:  # noqa: BLE001 - never expose key material or its path
        raise SheetsCredentialError(
            "Service-Account-Schlüssel fehlt oder ist ungültig."
        ) from None
    return AuthorizedSession(credentials)
