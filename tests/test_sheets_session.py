"""Synthetic key files only; no network access."""

import json

import pytest
from google.oauth2 import service_account

from portfolio_dashboard import sheets_session
from portfolio_dashboard.sheets_adapter import SHEETS_READONLY_SCOPE
from portfolio_dashboard.sheets_session import (
    SheetsCredentialError,
    build_readonly_session,
)


def test_missing_wrong_type_and_broken_files_fail_neutrally(tmp_path):
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"type": "authorized_user", "secret": "synthetic-secret"}))
    broken = tmp_path / "broken.json"
    broken.write_text("{ synthetic-secret")
    for path in (tmp_path / "missing.json", wrong, broken):
        with pytest.raises(SheetsCredentialError) as caught:
            build_readonly_session(str(path))
        assert "synthetic-secret" not in repr(caught.value)
        assert str(tmp_path) not in repr(caught.value)
        assert caught.value.__cause__ is None


def test_service_account_session_requests_only_the_readonly_scope(tmp_path, monkeypatch):
    key_file = tmp_path / "key.json"
    key_file.write_text(json.dumps({"type": "service_account", "synthetic": True}))
    seen = {}

    def fake(info, scopes):
        seen["scopes"] = scopes
        return service_account.Credentials(
            signer=None, service_account_email="synthetic@example.invalid",
            token_uri="https://example.invalid/token", scopes=scopes,
        )

    monkeypatch.setattr(
        sheets_session.service_account.Credentials, "from_service_account_info", fake
    )
    session = build_readonly_session(str(key_file))
    assert seen["scopes"] == [SHEETS_READONLY_SCOPE]
    assert session.credentials.scopes == [SHEETS_READONLY_SCOPE]
