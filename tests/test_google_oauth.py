"""Google OAuth regression tests. No test in this module contacts Google."""

import json
import os
import stat
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.tools import google_auth


@pytest.fixture()
def auth_store(tmp_path, monkeypatch):
    auth_dir = tmp_path / "google_auth"
    monkeypatch.setattr(google_auth, "AUTH_DIR", auth_dir)
    monkeypatch.setattr(google_auth, "TOKEN_FILE", auth_dir / "token.json")
    monkeypatch.setattr(google_auth, "STATE_DB", auth_dir / "oauth_states.sqlite3")
    monkeypatch.setattr(google_auth, "CREDS_FILE", auth_dir / "credentials.json")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI", "https://jarvis.example.test/api/google/callback"
    )
    return auth_dir


class FakeFlow:
    def __init__(self, state="", credentials=None):
        self.state = state
        self.credentials = credentials
        self.fetch_codes = []

    def authorization_url(self, **kwargs):
        query = (
            "state=" + kwargs["state"]
            + "&redirect_uri=https%3A%2F%2Fjarvis.example.test%2Fapi%2Fgoogle%2Fcallback"
        )
        return "https://accounts.google.test/auth?" + query, kwargs["state"]

    def fetch_token(self, code):
        self.fetch_codes.append(code)


def test_auth_start_creates_bound_single_use_state(auth_store, monkeypatch):
    monkeypatch.setattr(google_auth, "_flow", lambda state=None: FakeFlow(state))
    url = google_auth.begin_authorization("admin")
    query = parse_qs(urlparse(url).query)
    state_value = query["state"][0]

    assert query["redirect_uri"] == ["https://jarvis.example.test/api/google/callback"]
    assert "secret" not in state_value.lower()
    assert google_auth.consume_authorization_state(state_value) == "admin"
    assert google_auth.consume_authorization_state(state_value) is None


def test_unknown_missing_and_expired_states_are_rejected(auth_store):
    assert google_auth.consume_authorization_state("") is None
    assert google_auth.consume_authorization_state("unknown") is None
    with google_auth._state_connection() as conn:
        conn.execute(
            "INSERT INTO oauth_states(state, username, expires_at) VALUES (?, ?, ?)",
            ("expired", "admin", int(time.time()) - 1),
        )
    assert google_auth.consume_authorization_state("expired") is None


def _credentials(refresh_token="refresh-new"):
    return SimpleNamespace(
        token="access-value",
        refresh_token=refresh_token,
        token_uri=google_auth.TOKEN_URI,
        client_id="test-client-id",
        scopes=list(google_auth.SCOPES),
        expiry=datetime(2030, 1, 1),
    )


def test_token_write_is_canonical_private_and_preserves_refresh(auth_store):
    google_auth._save_token(_credentials("refresh-old"), email="user@example.test")
    google_auth._save_token(_credentials(None), email="user@example.test")
    data = json.loads(google_auth.TOKEN_FILE.read_text(encoding="utf-8"))

    assert set(data) == {
        "access_token", "refresh_token", "token_uri", "client_id", "scopes",
        "expiry", "email",
    }
    assert data["refresh_token"] == "refresh-old"
    assert "client_secret" not in data
    assert not list(auth_store.glob(".token-*.json"))
    if os.name == "posix":
        assert stat.S_IMODE(google_auth.TOKEN_FILE.stat().st_mode) == 0o600
        assert stat.S_IMODE(auth_store.stat().st_mode) == 0o700


def test_exchange_uses_one_store_and_preserves_refresh(auth_store, monkeypatch):
    google_auth._save_token(_credentials("refresh-old"), email="old@example.test")
    flow = FakeFlow(credentials=_credentials(None))
    monkeypatch.setattr(google_auth, "_flow", lambda state=None: flow)
    monkeypatch.setattr(google_auth, "_get_email", lambda creds: "new@example.test")

    assert google_auth.exchange_authorization_code("one-time-code", "state") == "new@example.test"
    stored = json.loads(google_auth.TOKEN_FILE.read_text(encoding="utf-8"))
    assert flow.fetch_codes == ["one-time-code"]
    assert stored["refresh_token"] == "refresh-old"
    assert stored["email"] == "new@example.test"


def test_canonical_credentials_load_for_all_services(auth_store):
    google_auth._save_token(_credentials("refresh-value"), email="user@example.test")
    creds = google_auth._load_credentials()

    assert creds.token == "access-value"
    assert creds.refresh_token == "refresh-value"
    assert creds.client_secret == "test-client-secret"
    assert set(creds.scopes) == set(google_auth.SCOPES)


def test_all_google_tools_use_canonical_service_source():
    for name in ("google_gmail.py", "google_drive.py", "google_calendar.py"):
        source = (ROOT / "backend" / "tools" / name).read_text(encoding="utf-8")
        assert "from backend.tools.google_auth import get_service" in source


def test_routes_settings_and_gog_security_contracts():
    main = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    frontend = (ROOT / "frontend" / "js" / "google.js").read_text(encoding="utf-8")

    assert '@app.post("/api/google/auth/start")' in main
    start_block = main.split('@app.post("/api/google/auth/start")', 1)[1][:500]
    assert "require_local_auth" in start_block
    assert '@app.get("/api/google/callback")' in main
    callback = main.split('@app.get("/api/google/callback")', 1)[1].split(
        '@app.post("/api/google/device-start")', 1
    )[0]
    assert "require_local_auth" not in callback
    assert "consume_authorization_state" in callback
    assert "RedirectResponse" in callback
    assert "request.query_params.get(\"next\"" not in callback
    for error_code in ("invalid_state", "missing_code", "exchange_failed", "success"):
        assert f"google_oauth={error_code}" in callback
    assert 'safe_error = "access_denied"' in callback
    assert 'else "provider_error"' in callback
    assert "code}" not in callback
    assert "state}" not in callback
    assert "client_secret_configured" in main
    assert 'cfg.pop("client_secret", None)' in main
    assert "body.get(\"client_secret\") or os.environ.get" in main
    assert "/api/google/auth/start" in frontend
    assert "/api/google/device-start" not in frontend

    gog = main.split("# ─── OpenClaw Gmail (gog) Setup-Endpoints", 1)[1]
    assert '"redirect_uris":  ["http://localhost"]' in gog
    assert '"--services", "gmail,calendar,drive"' in gog
