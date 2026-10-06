"""Canonical server-side Google OAuth implementation for Jarvis."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent.parent
AUTH_DIR = PROJECT_ROOT / "data" / "google_auth"
CREDS_FILE = AUTH_DIR / "credentials.json"
TOKEN_FILE = AUTH_DIR / "token.json"
STATE_DB = AUTH_DIR / "oauth_states.sqlite3"
TOKEN_URI = "https://oauth2.googleapis.com/token"
STATE_TTL_SECONDS = 600

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar",
]

_lock = threading.RLock()


def get_redirect_uri() -> str:
    """Return the explicit callback URL, with a localhost-only dev fallback."""
    return os.getenv(
        "GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/api/google/callback"
    ).strip()


def _client_config() -> tuple[str, str]:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return client_id, client_secret
    if CREDS_FILE.exists():
        try:
            raw = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
            cfg = raw.get("web") or raw.get("installed") or {}
            return str(cfg.get("client_id", "")), str(cfg.get("client_secret", ""))
        except (OSError, ValueError, TypeError):
            pass
    return "", ""


def credentials_exist() -> bool:
    return all(_client_config())


def _flow(*, state: Optional[str] = None):
    from google_auth_oauthlib.flow import Flow

    client_id, client_secret = _client_config()
    if not client_id or not client_secret:
        raise RuntimeError("Google OAuth ist nicht konfiguriert")
    config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": TOKEN_URI,
            "redirect_uris": [get_redirect_uri()],
        }
    }
    return Flow.from_client_config(
        config, scopes=SCOPES, state=state, redirect_uri=get_redirect_uri()
    )


def _prepare_private_dir() -> None:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(AUTH_DIR, 0o700)


def _state_connection() -> sqlite3.Connection:
    _prepare_private_dir()
    conn = sqlite3.connect(STATE_DB, timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS oauth_states ("
        "state TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at INTEGER NOT NULL)"
    )
    if os.name == "posix":
        os.chmod(STATE_DB, 0o600)
    return conn


def begin_authorization(username: str) -> str:
    """Create a short-lived state bound to an authenticated Jarvis admin."""
    state = secrets.token_urlsafe(32)
    now = int(time.time())
    flow = _flow(state=state)
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    with _lock, _state_connection() as conn:
        conn.execute("DELETE FROM oauth_states WHERE expires_at <= ?", (now,))
        conn.execute(
            "INSERT INTO oauth_states(state, username, expires_at) VALUES (?, ?, ?)",
            (state, username, now + STATE_TTL_SECONDS),
        )
    return url


def consume_authorization_state(state: str) -> Optional[str]:
    """Atomically consume a valid state and return its initiating username."""
    if not state:
        return None
    now = int(time.time())
    with _lock, _state_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT username, expires_at FROM oauth_states WHERE state = ?", (state,)
        ).fetchone()
        conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
        conn.execute("DELETE FROM oauth_states WHERE expires_at <= ?", (now,))
        conn.commit()
    if not row or int(row[1]) <= now:
        return None
    return str(row[0])


def exchange_authorization_code(code: str, state: str) -> str:
    """Exchange a code server-side, persist credentials, and return account email."""
    flow = _flow(state=state)
    flow.fetch_token(code=code)
    creds = flow.credentials
    existing = _read_token()
    if not creds.refresh_token and existing:
        creds.refresh_token = existing.get("refresh_token") or None
    email = _get_email(creds)
    _save_token(creds, email=email)
    return email


def get_auth_url() -> str:
    """Backward-compatible helper; callers should use begin_authorization()."""
    return begin_authorization("legacy")


def handle_callback(code: str) -> dict:
    """Deprecated compatibility helper without state handling."""
    return {"error": "Callback muss ueber /api/google/callback erfolgen"}


def is_authenticated() -> bool:
    creds = _load_credentials()
    return creds is not None and bool(creds.valid or creds.refresh_token)


def get_status() -> dict:
    client_id, client_secret = _client_config()
    result = {
        "configured": bool(client_id and client_secret),
        "authenticated": is_authenticated(),
        "email": None,
    }
    token = _read_token()
    if token and result["authenticated"]:
        result["email"] = token.get("email") or None
    return result


def get_service(api_name: str, version: str):
    from googleapiclient.discovery import build

    creds = _load_credentials()
    if creds is None:
        raise RuntimeError("Nicht authentifiziert. Bitte zuerst mit Google verbinden.")
    _maybe_refresh(creds)
    return build(api_name, version, credentials=creds)


def revoke() -> bool:
    try:
        creds = _load_credentials()
        if creds and creds.token:
            import requests

            requests.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": creds.token},
                headers={"content-type": "application/x-www-form-urlencoded"},
                timeout=5,
            )
    except Exception:
        pass
    with _lock:
        TOKEN_FILE.unlink(missing_ok=True)
    return True


def _read_token() -> Optional[dict]:
    if not TOKEN_FILE.exists():
        return None
    try:
        with _lock:
            return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _load_credentials():
    data = _read_token()
    if not data:
        return None
    from google.oauth2.credentials import Credentials

    client_id, client_secret = _client_config()
    expiry = data.get("expiry")
    parsed_expiry = None
    if expiry:
        try:
            parsed_expiry = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
            parsed_expiry = parsed_expiry.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            parsed_expiry = None
    return Credentials(
        token=data.get("access_token") or data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri", TOKEN_URI),
        client_id=client_id or data.get("client_id"),
        client_secret=client_secret,
        scopes=data.get("scopes", SCOPES),
        expiry=parsed_expiry,
    )


def _save_token(creds, *, email: str = "") -> None:
    previous = _read_token() or {}
    refresh_token = creds.refresh_token or previous.get("refresh_token")
    data = {
        "access_token": creds.token,
        "refresh_token": refresh_token,
        "token_uri": creds.token_uri or TOKEN_URI,
        "client_id": creds.client_id,
        "scopes": list(creds.scopes or SCOPES),
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
        "email": email or previous.get("email", ""),
    }
    _prepare_private_dir()
    with _lock:
        fd, tmp_name = tempfile.mkstemp(prefix=".token-", suffix=".json", dir=AUTH_DIR)
        try:
            if os.name == "posix":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, TOKEN_FILE)
            if os.name == "posix":
                os.chmod(TOKEN_FILE, 0o600)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)


def _maybe_refresh(creds) -> None:
    if creds and not creds.valid and creds.refresh_token:
        from google.auth.transport.requests import Request

        old_refresh_token = creds.refresh_token
        creds.refresh(Request())
        if not creds.refresh_token:
            creds.refresh_token = old_refresh_token
        _save_token(creds)


def _get_email(creds) -> str:
    try:
        from googleapiclient.discovery import build

        info = build("oauth2", "v2", credentials=creds).userinfo().get().execute()
        return str(info.get("email", ""))
    except Exception:
        return ""
