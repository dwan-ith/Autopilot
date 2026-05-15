"""OAuth2 Token Manager for AUTOPILOT connectors.

Supports Google OAuth2 (Gmail, Google Drive) with:
- Authorization URL generation (PKCE-ready)
- Authorization code → token exchange
- Automatic token refresh when expired
- Token persistence in the connector_connections table (credentials_ref field)

Env vars needed:
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  AUTOPILOT_OAUTH_REDIRECT_URI   (default: http://localhost:8090/oauth/callback/google)
"""

from __future__ import annotations

import json
import os
import time
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

# ── Constants ────────────────────────────────────────────────────────────────

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

SCOPES_GMAIL = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

SCOPES_DRIVE = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

SCOPES_COMBINED = list(dict.fromkeys(SCOPES_GMAIL + SCOPES_DRIVE))  # deduplicated


def _redirect_uri() -> str:
    return os.getenv(
        "AUTOPILOT_OAUTH_REDIRECT_URI",
        "http://localhost:8090/oauth/callback/google",
    )


def _client_id() -> str:
    return os.getenv("GOOGLE_CLIENT_ID", "")


def _client_secret() -> str:
    return os.getenv("GOOGLE_CLIENT_SECRET", "")


def google_configured() -> bool:
    return bool(_client_id() and _client_secret())


# ── Authorization URL ─────────────────────────────────────────────────────────

def build_google_auth_url(state: str, scopes: list[str] | None = None) -> str:
    """Return the Google OAuth2 consent-screen URL.

    Pass ``state`` as a URL-safe string to identify the originating connector
    (e.g. ``"gmail"`` or ``"google_drive"``).
    """
    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": " ".join(scopes or SCOPES_COMBINED),
        "access_type": "offline",   # get refresh_token
        "prompt": "consent",        # force consent so refresh_token is always returned
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


# ── Token Exchange & Refresh ──────────────────────────────────────────────────

async def exchange_code(code: str) -> dict[str, Any]:
    """Exchange an authorization code for access + refresh tokens."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        token = resp.json()
        token["obtained_at"] = int(time.time())
        return token


async def refresh_token(token_data: dict[str, Any]) -> dict[str, Any]:
    """Refresh an expired access token using the stored refresh_token."""
    refresh = token_data.get("refresh_token", "")
    if not refresh:
        raise ValueError("No refresh_token available — user must re-authorize.")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "refresh_token": refresh,
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        new_token = resp.json()
        # Merge — Google doesn't return refresh_token again on refresh
        merged = {**token_data, **new_token, "obtained_at": int(time.time())}
        return merged


def is_expired(token_data: dict[str, Any], buffer_secs: int = 120) -> bool:
    """Return True if the access token will expire within buffer_secs."""
    obtained_at = token_data.get("obtained_at", 0)
    expires_in = token_data.get("expires_in", 3600)
    return int(time.time()) >= (obtained_at + expires_in - buffer_secs)


# ── Token Storage (via connector_connections.credentials_ref) ─────────────────

class OAuthTokenStore:
    """Lightweight token persistence using the existing SQLite store."""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path

    def _connect(self):  # type: ignore[return]
        import sqlite3
        conn = sqlite3.connect(self._path, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, connector_id: str, token_data: dict[str, Any]) -> None:
        payload = json.dumps(token_data)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                insert or replace into connector_connections
                    (connector_id, status, auth_mode, granted_scopes, connected_at, credentials_ref, metadata)
                values (?, 'connected', 'oauth', ?, datetime('now'), ?, '{}')
                on conflict(connector_id) do update set
                    status = 'connected',
                    credentials_ref = excluded.credentials_ref,
                    connected_at = excluded.connected_at
                """,
                (connector_id, json.dumps(token_data.get("scope", "").split()), payload),
            )

    def load(self, connector_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "select credentials_ref from connector_connections where connector_id = ?",
                (connector_id,),
            ).fetchone()
            if row and row["credentials_ref"]:
                try:
                    return json.loads(row["credentials_ref"])
                except json.JSONDecodeError:
                    return None
            return None

    def delete(self, connector_id: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "update connector_connections set status='disconnected', credentials_ref=null where connector_id=?",
                (connector_id,),
            )


# ── Convenience: get a valid access token ────────────────────────────────────

async def get_valid_token(connector_id: str, db_path: Path) -> str | None:
    """Return a valid access_token, refreshing if needed. Returns None if not authorized."""
    store = OAuthTokenStore(db_path)
    token_data = store.load(connector_id)
    if not token_data:
        return None
    if is_expired(token_data):
        try:
            token_data = await refresh_token(token_data)
            store.save(connector_id, token_data)
        except Exception:
            return None
    return token_data.get("access_token")


def is_authorized(connector_id: str, db_path: Path) -> bool:
    """Check synchronously if a connector has stored OAuth tokens."""
    store = OAuthTokenStore(db_path)
    token_data = store.load(connector_id)
    return bool(token_data and token_data.get("access_token"))
