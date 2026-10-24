"""Typed runtime settings without UI, provider imports, or credential-file reads.

Demo is the default and deliberately discards all real source/login settings.
Reading configuration is not authentication. Production also requires the
SDK-validated session and explicit owner authorization on every app run.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """A safe configuration error containing field names, never field values."""


class Mode(StrEnum):
    DEMO = "demo"
    PRODUCTION = "production"


class SourceKind(StrEnum):
    SYNTHETIC = "synthetic"
    GOOGLE_SHEETS = "google_sheets"


class Provider(StrEnum):
    SYNTHETIC = "synthetic"
    YFINANCE = "yfinance"


@dataclass(frozen=True, slots=True)
class CacheTTLs:
    quotes_seconds: int = 60
    holdings_seconds: int = 300
    history_seconds: int = 21_600


@dataclass(frozen=True, slots=True)
class SourceConfig:
    kind: SourceKind = SourceKind.SYNTHETIC
    spreadsheet_id: str | None = field(default=None, repr=False)
    worksheet_range: str | None = field(default=None, repr=False)
    credentials_file: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class LoginConfig:
    enabled: bool = False
    owner_issuer: str | None = field(default=None, repr=False)
    owner_subject: str | None = field(default=None, repr=False)
    client_id: str | None = field(default=None, repr=False)
    client_secret: str | None = field(default=None, repr=False)
    cookie_secret: str | None = field(default=None, repr=False)
    redirect_uri: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class AppConfig:
    mode: Mode
    source: SourceConfig
    provider: Provider
    ttls: CacheTTLs
    login: LoginConfig


def _text(values: Mapping[str, object], name: str, default: str = "") -> str:
    value = values.get(name, default)
    if not isinstance(value, str):
        raise ConfigurationError(f"Konfiguration muss Text enthalten: {name}.")
    return value.strip()


def _required(values: Mapping[str, object], name: str) -> str:
    value = _text(values, name)
    if not value or value.startswith("<") or value == "CHANGE_ME":
        raise ConfigurationError(f"Produktivkonfiguration fehlt: {name}.")
    return value


def _positive_seconds(values: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(_text(values, name, str(default)))
    except ValueError:
        raise ConfigurationError(f"Positive ganze Sekunden erforderlich: {name}.") from None
    if value <= 0:
        raise ConfigurationError(f"Positive ganze Sekunden erforderlich: {name}.")
    return value


GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"


def load_config(
    values: Mapping[str, str] | None = None,
    *,
    auth_settings: Mapping[str, object] | None = None,
) -> AppConfig:
    """Load exported settings; do not read .env or any credential file.

    ``values`` permits deterministic callers and tests. Secrets/source settings
    are ignored in demo mode, even if inherited from the process environment.
    Production OIDC settings must come from Streamlit's server-side [auth]
    section, which its supported login/callback implementation also uses.
    """
    values = os.environ if values is None else values
    try:
        mode = Mode(_text(values, "PORTFOLIO_MODE", Mode.DEMO))
    except ValueError:
        raise ConfigurationError("Ungültiger Betriebsmodus: PORTFOLIO_MODE.") from None
    ttls = CacheTTLs(
        quotes_seconds=_positive_seconds(values, "PORTFOLIO_QUOTES_TTL_SECONDS", 60),
        holdings_seconds=_positive_seconds(values, "PORTFOLIO_HOLDINGS_TTL_SECONDS", 300),
        history_seconds=_positive_seconds(values, "PORTFOLIO_HISTORY_TTL_SECONDS", 21_600),
    )
    if mode is Mode.DEMO:
        return AppConfig(mode, SourceConfig(), Provider.SYNTHETIC, ttls, LoginConfig())

    # Supported implementations are explicit; no automatic provider selection.
    if _text(values, "PORTFOLIO_SOURCE", SourceKind.GOOGLE_SHEETS) != SourceKind.GOOGLE_SHEETS:
        raise ConfigurationError("Nicht unterstützte Quelle: PORTFOLIO_SOURCE.")
    if _text(values, "PORTFOLIO_PROVIDER", Provider.YFINANCE) != Provider.YFINANCE:
        raise ConfigurationError("Nicht unterstützter Provider: PORTFOLIO_PROVIDER.")
    if _text(values, "PORTFOLIO_LOGIN_PROVIDER", "google") != "google":
        raise ConfigurationError("Nicht unterstützter Login: PORTFOLIO_LOGIN_PROVIDER.")

    owner_issuer = _required(values, "PORTFOLIO_OWNER_ISSUER")
    if owner_issuer not in {"https://accounts.google.com", "accounts.google.com"}:
        raise ConfigurationError("Ungültiger Google-Issuer: PORTFOLIO_OWNER_ISSUER.")
    if not isinstance(auth_settings, Mapping):
        raise ConfigurationError("Produktivkonfiguration fehlt: auth.")
    # One default Google provider; do not allow SDK/client validation overrides.
    if set(auth_settings) - {
        "client_id", "client_secret", "cookie_secret", "redirect_uri",
        "server_metadata_url",
    }:
        raise ConfigurationError("Nicht unterstützte Konfiguration: auth.")
    if _required(auth_settings, "server_metadata_url") != GOOGLE_METADATA_URL:
        raise ConfigurationError("Ungültiger Google-Metadatenendpunkt: auth.server_metadata_url.")
    login = LoginConfig(
        enabled=True,
        owner_issuer=owner_issuer,
        owner_subject=_required(values, "PORTFOLIO_OWNER_SUBJECT"),
        client_id=_required(auth_settings, "client_id"),
        client_secret=_required(auth_settings, "client_secret"),
        cookie_secret=_required(auth_settings, "cookie_secret"),
        redirect_uri=_required(auth_settings, "redirect_uri"),
    )
    try:
        redirect = urlsplit(login.redirect_uri)
    except ValueError:
        raise ConfigurationError("HTTPS-Callback erforderlich: auth.redirect_uri.") from None
    if (
        redirect.scheme != "https" or not redirect.hostname
        or redirect.username or redirect.password or redirect.query or redirect.fragment
        or not redirect.path.endswith("/oauth2callback")
    ):
        raise ConfigurationError("HTTPS-Callback erforderlich: auth.redirect_uri.")
    source = SourceConfig(
        kind=SourceKind.GOOGLE_SHEETS,
        spreadsheet_id=_required(values, "PORTFOLIO_SHEET_ID"),
        worksheet_range=_required(values, "PORTFOLIO_SHEET_RANGE"),
        credentials_file=_required(values, "PORTFOLIO_SHEETS_CREDENTIALS_FILE"),
    )
    return AppConfig(mode, source, Provider.YFINANCE, ttls, login)
