"""Owner authorization using only Streamlit's validated OIDC session.

This module does not parse tokens, trust request parameters, or persist grants.
Streamlit/Authlib handle signatures, issuer/audience validation, nonce and state.
The app rechecks the allowlist and token lifetime before any protected work.
"""

import math
import time
from collections.abc import Mapping

import streamlit as st

from portfolio_dashboard.config import LoginConfig


def _numeric_date(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def owner_is_authorized(
    user: Mapping[str, object], login: LoginConfig, *, now: float | None = None
) -> bool:
    """Check SDK-supplied claims, or synthetic equivalents in offline tests.

    A dict supplied by a browser is never an authentication source. The app
    passes only st.user, whose claims come from Streamlit's signed identity
    cookie after Authlib's OIDC callback validation. No email-based fallback.
    """
    now = time.time() if now is None else now
    if (
        not login.enabled or not login.owner_issuer or not login.owner_subject
        or not login.client_id or not _numeric_date(now)
        or user.get("is_logged_in") is not True
        or user.get("iss") != login.owner_issuer
        or user.get("sub") != login.owner_subject
    ):
        return False
    issued, expires = user.get("iat"), user.get("exp")
    if (
        not _numeric_date(issued) or not _numeric_date(expires)
        or not 0 <= issued <= now < expires
    ):
        return False
    audience = user.get("aud")
    if "azp" in user and user["azp"] != login.client_id:
        return False
    if isinstance(audience, str):
        return audience == login.client_id
    return (
        isinstance(audience, list) and bool(audience)
        and all(isinstance(item, str) for item in audience)
        and login.client_id in audience
        and (len(audience) == 1 or user.get("azp") == login.client_id)
    )


def require_owner(login: LoginConfig) -> None:
    """Stop the current run before loaders, renderers, and exports unless allowed."""
    user = st.user
    logged_in = user.get("is_logged_in") is True
    allowed = owner_is_authorized(user, login)
    if logged_in:
        if st.button("Von dieser App abmelden", key="auth.logout"):
            try:
                st.logout()
            except Exception:
                st.error("App-Abmeldung nicht verfügbar. Sitzung schließen und Server prüfen lassen.")
            st.stop()  # Never fall through to data even before redirect completes.
        st.caption(
            "App-Abmeldung beendet diese Sitzung und entfernt das Browser-Cookie. "
            "Andere offene Tabs werden nicht automatisch abgemeldet; "
            "Freigabe und Tokenablauf werden bei jedem App-Lauf geprüft. "
            "Das Google-Konto bleibt angemeldet."
        )
    if allowed:
        return
    st.error("Kein Besitzerzugriff. Anmeldung, Freigabe oder Sitzung fehlt bzw. ist ungültig.")
    if not logged_in and st.button("Mit Google anmelden", key="auth.login"):
        # SDK errors can contain provider/config details; never display those.
        try:
            st.login()
        except Exception:
            st.error("Anmeldung nicht verfügbar. Serverkonfiguration prüfen lassen.")
    st.stop()
