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

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

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
_OAUTH_STATE_FALLBACK_SECRET = secrets.token_bytes(32)
_OAUTH_STATE_TTL_SECONDS = 10 * 60
_ENCRYPTED_TOKEN_PREFIX = "fernet:v1:"


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _urlsafe_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _oauth_state_secret() -> bytes:
    configured = (
        os.getenv("AUTOPILOT_OAUTH_STATE_SECRET", "").strip()
        or os.getenv("AUTOPILOT_API_KEY", "").strip()
    )
    return configured.encode("utf-8") if configured else _OAUTH_STATE_FALLBACK_SECRET


def _token_fernet() -> Fernet | None:
    material = (
        os.getenv("AUTOPILOT_CREDENTIAL_ENCRYPTION_KEY", "").strip()
        or os.getenv("AUTOPILOT_API_KEY", "").strip()
    )
    if not material:
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(material.encode("utf-8")).digest())
    return Fernet(key)


def oauth_token_encryption_enabled() -> bool:
    return _token_fernet() is not None


def _encode_token_payload(token_data: dict[str, Any]) -> str:
    raw = json.dumps(token_data, separators=(",", ":")).encode("utf-8")
    fernet = _token_fernet()
    if fernet is None:
        return raw.decode("utf-8")
    return _ENCRYPTED_TOKEN_PREFIX + fernet.encrypt(raw).decode("ascii")


def _decode_token_payload(payload: str) -> dict[str, Any] | None:
    raw = payload
    if payload.startswith(_ENCRYPTED_TOKEN_PREFIX):
        fernet = _token_fernet()
        if fernet is None:
            return None
        try:
            raw = fernet.decrypt(
                payload[len(_ENCRYPTED_TOKEN_PREFIX):].encode("ascii")
            ).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError):
            return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def build_oauth_state(provider: str, connector_id: str) -> str:
    """Create a signed, short-lived OAuth state token."""
    payload = {
        "provider": provider,
        "connector_id": connector_id,
        "issued_at": int(time.time()),
        "nonce": secrets.token_urlsafe(16),
    }
    encoded = _urlsafe_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(_oauth_state_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_urlsafe_encode(signature)}"


def verify_oauth_state(state: str | None, provider: str) -> str:
    """Validate OAuth state integrity, age, provider, and connector scope."""
    if not state or "." not in state:
        raise ValueError("missing_or_invalid_oauth_state")
    encoded, supplied_signature = state.split(".", 1)
    expected = hmac.new(_oauth_state_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    try:
        supplied = _urlsafe_decode(supplied_signature)
        payload = json.loads(_urlsafe_decode(encoded))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid_oauth_state") from exc
    if not hmac.compare_digest(expected, supplied):
        raise ValueError("invalid_oauth_state")
    issued_at = int(payload.get("issued_at", 0))
    if issued_at <= 0 or abs(int(time.time()) - issued_at) > _OAUTH_STATE_TTL_SECONDS:
        raise ValueError("expired_oauth_state")
    if payload.get("provider") != provider:
        raise ValueError("oauth_provider_mismatch")
    connector_id = str(payload.get("connector_id", ""))
    allowed = {"github"} if provider == "github" else {"gmail", "google_drive", "google"}
    if connector_id not in allowed:
        raise ValueError("invalid_oauth_connector")
    return connector_id


def _scopes_json(token_data: dict[str, Any]) -> str:
    """Normalize OAuth ``scope`` field into JSON list for ``connector_connections.granted_scopes``."""
    raw = token_data.get("scope")
    if isinstance(raw, list):
        return json.dumps([str(x) for x in raw])
    if isinstance(raw, str):
        parts = [p for p in raw.replace(",", " ").split() if p]
        return json.dumps(parts)
    return json.dumps([])


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


def github_configured() -> bool:
    return bool(os.getenv("GITHUB_CLIENT_ID") and os.getenv("GITHUB_CLIENT_SECRET"))


def _github_redirect_uri() -> str:
    """Must match the redirect URI registered on the GitHub OAuth App."""
    explicit = os.getenv("AUTOPILOT_GITHUB_OAUTH_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    base_google = os.getenv("AUTOPILOT_OAUTH_REDIRECT_URI", "")
    if base_google and "google" in base_google:
        return base_google.replace("google", "github")
    return "http://localhost:8090/oauth/callback/github"


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


def build_github_auth_url(state: str) -> str:
    """Return the GitHub App OAuth authorization URL."""
    redirect_uri = _github_redirect_uri()
    params = {
        "client_id": os.getenv("GITHUB_CLIENT_ID", ""),
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"https://github.com/login/oauth/authorize?{urlencode(params)}"


# ── Token Exchange & Refresh ──────────────────────────────────────────────────

def _validate_google_token_payload(token: dict[str, Any]) -> dict[str, Any]:
    """Google returns 4xx on many failures; when JSON includes ``error``, treat as failure."""
    if token.get("error"):
        raise ValueError(token.get("error_description") or str(token["error"]))
    if not token.get("access_token"):
        raise ValueError("Google OAuth response missing access_token")
    return token


def _validate_github_token_payload(token: dict[str, Any]) -> dict[str, Any]:
    """GitHub returns HTTP 200 with either ``access_token`` or ``error`` — must check body."""
    if token.get("error"):
        raise ValueError(token.get("error_description") or str(token["error"]))
    if not token.get("access_token"):
        raise ValueError("GitHub OAuth response missing access_token")
    return token


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
        token = _validate_google_token_payload(resp.json())
        token["obtained_at"] = int(time.time())
        return token


async def exchange_github_code(code: str) -> dict[str, Any]:
    """Exchange a GitHub authorization code for an access token."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": os.getenv("GITHUB_CLIENT_ID", ""),
                "client_secret": os.getenv("GITHUB_CLIENT_SECRET", ""),
                "code": code,
                "redirect_uri": _github_redirect_uri(),
            },
            headers={"Accept": "application/json"}
        )
        resp.raise_for_status()
        token = _validate_github_token_payload(resp.json())
        token["obtained_at"] = int(time.time())
        return token


def mirror_google_oauth_to_siblings(
    token_store: OAuthTokenStore,
    token_data: dict[str, Any],
    user_id: str,
    origin_connector_id: str,
) -> None:
    """When consent includes both Gmail and Drive scopes, duplicate tokens so both cards show connected."""
    scope_blob = str(token_data.get("scope", ""))
    if origin_connector_id == "gmail" and ("drive.googleapis.com" in scope_blob or "/auth/drive" in scope_blob):
        token_store.save("google_drive", token_data, user_id)
    if origin_connector_id == "google_drive" and "gmail.googleapis.com" in scope_blob:
        token_store.save("gmail", token_data, user_id)


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

    def save(self, connector_id: str, token_data: dict[str, Any], user_id: str = "default_user") -> None:
        payload = _encode_token_payload(token_data)
        conn = self._connect()
        try:
            conn.execute(
                """
                insert or replace into connector_connections
                    (connector_id, user_id, status, auth_mode, granted_scopes, connected_at, credentials_ref, metadata)
                values (?, ?, 'connected', 'oauth', ?, datetime('now'), ?, '{}')
                on conflict(connector_id, user_id) do update set
                    status = excluded.status,
                    auth_mode = excluded.auth_mode,
                    granted_scopes = excluded.granted_scopes,
                    credentials_ref = excluded.credentials_ref,
                    connected_at = excluded.connected_at
                """,
                (connector_id, user_id, _scopes_json(token_data), payload),
            )
            conn.commit()
        finally:
            conn.close()

    def load(self, connector_id: str, user_id: str = "default_user") -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "select credentials_ref from connector_connections where connector_id = ? and user_id = ?",
                (connector_id, user_id),
            ).fetchone()
            if row and row["credentials_ref"]:
                return _decode_token_payload(row["credentials_ref"])
            return None
        finally:
            conn.close()

    def delete(self, connector_id: str, user_id: str = "default_user") -> None:
        conn = self._connect()
        try:
            conn.execute(
                "update connector_connections set status='disconnected', credentials_ref=null where connector_id=? and user_id=?",
                (connector_id, user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def encrypt_existing(self) -> int:
        """Encrypt legacy plaintext OAuth rows after a key is configured."""
        if not oauth_token_encryption_enabled():
            return 0
        conn = self._connect()
        migrated = 0
        try:
            rows = conn.execute(
                """
                select connector_id, user_id, credentials_ref
                from connector_connections
                where auth_mode='oauth' and credentials_ref is not null
                """
            ).fetchall()
            for row in rows:
                payload = str(row["credentials_ref"])
                if payload.startswith(_ENCRYPTED_TOKEN_PREFIX):
                    continue
                token_data = _decode_token_payload(payload)
                if token_data is None:
                    continue
                conn.execute(
                    """
                    update connector_connections
                    set credentials_ref=?
                    where connector_id=? and user_id=?
                    """,
                    (
                        _encode_token_payload(token_data),
                        row["connector_id"],
                        row["user_id"],
                    ),
                )
                migrated += 1
            conn.commit()
            return migrated
        finally:
            conn.close()


# ── Convenience: get a valid access token ────────────────────────────────────

async def get_valid_token(connector_id: str, db_path: Path, user_id: str = "default_user") -> str | None:
    """Return a valid access_token, refreshing if needed. Returns None if not authorized."""
    store = OAuthTokenStore(db_path)
    token_data = store.load(connector_id, user_id)
    if not token_data:
        return None
    if is_expired(token_data):
        try:
            token_data = await refresh_token(token_data)
            store.save(connector_id, token_data, user_id)
        except Exception:
            return None
    return token_data.get("access_token")


def is_authorized(connector_id: str, db_path: Path, user_id: str = "default_user") -> bool:
    """Check synchronously if a connector has stored OAuth tokens."""
    store = OAuthTokenStore(db_path)
    token_data = store.load(connector_id, user_id)
    return bool(token_data and token_data.get("access_token"))
