# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Hermetic tests for the Freebuff account connector (no real network)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import httpx
import pytest

from artemis.services import freebuff as fb


def _write_credentials(tmp_path: Path, nested: bool = True) -> Path:
    profile = {
        "id": "11111111-2222-3333-4444-555555555555",
        "name": "tester",
        "email": "tester@example.com",
        "authToken": "token-abc123",
    }
    payload = {"default": profile} if nested else profile
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _me_route(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/api/v1/me"
    auth = request.headers.get("Authorization", "")
    if auth != "Bearer token-abc123":
        return httpx.Response(401, json={"error": "Missing or invalid Authorization header"})
    return httpx.Response(200, json={"id": "user-1", "email": "tester@example.com"})


def _transport(session_payload: dict | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/me":
            return _me_route(request)
        if request.url.path == "/api/v1/freebuff/session":
            if session_payload is None:
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(200, json=session_payload)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


# Captured before any monkeypatching so the mock factory can build real
# clients (patching fb.httpx.Client replaces the attribute on the shared
# httpx module object).
_REAL_CLIENT = httpx.Client


def _mock_client_factory(session_payload: dict | None = None):
    def factory(**kwargs):
        return _REAL_CLIENT(transport=_transport(session_payload), **kwargs)

    return factory


def _patch_client(monkeypatch: pytest.MonkeyPatch, session_payload: dict | None = None):
    monkeypatch.setattr(fb.httpx, "Client", _mock_client_factory(session_payload))


SESSION_PAYLOAD = {
    "status": "ended",
    "accessTier": "limited",
    "countryCode": "ID",
    "countryBlockReason": "country_not_allowed",
    "freebucks": {
        "balance": 10,
        "daily": {"limit": 25, "spent": 15, "remaining": 10, "resetAt": "2026-09-13T07:00:00.000Z"},
    },
}


class TestLoadCredentials:
    def test_loads_nested_default_profile(self, tmp_path: Path):
        path = _write_credentials(tmp_path)
        creds = fb.load_credentials(path)
        assert creds.auth_token == "token-abc123"
        assert creds.email == "tester@example.com"
        assert creds.user_id.startswith("1111")
        assert creds.source_path == path

    def test_loads_flat_profile(self, tmp_path: Path):
        path = _write_credentials(tmp_path, nested=False)
        creds = fb.load_credentials(path)
        assert creds.auth_token == "token-abc123"

    def test_missing_file_raises_filenotfound(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            fb.load_credentials(tmp_path / "nope.json")

    def test_invalid_json_raises_valueerror(self, tmp_path: Path):
        path = tmp_path / "credentials.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError):
            fb.load_credentials(path)

    def test_missing_auth_token_raises_valueerror(self, tmp_path: Path):
        path = tmp_path / "credentials.json"
        path.write_text(json.dumps({"default": {"email": "x@y.z"}}), encoding="utf-8")
        with pytest.raises(ValueError, match="authToken"):
            fb.load_credentials(path)

    def test_env_override_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        path = _write_credentials(tmp_path)
        monkeypatch.setenv(fb.CREDENTIALS_PATH_ENV, str(path))
        assert fb.credentials_path() == path
        creds = fb.load_credentials()
        assert creds.auth_token == "token-abc123"


class TestVerifyConnection:
    def test_success_with_session(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _patch_client(monkeypatch, SESSION_PAYLOAD)
        creds = fb.load_credentials(_write_credentials(tmp_path))
        status = fb.verify_connection(creds)

        assert status.connected is True
        assert status.email == "tester@example.com"
        assert status.access_tier == "limited"
        assert status.session_status == "ended"
        assert status.country_code == "ID"
        assert status.country_block_reason == "country_not_allowed"
        assert status.freebucks_balance == 10
        assert status.freebucks_daily_remaining == 10
        assert status.freebucks_daily_limit == 25
        assert status.freebucks_daily_reset_at == "2026-09-13T07:00:00.000Z"
        # Documented limitation: no LLM routing over freebuff credentials.
        assert status.llm_routing_available is False

    def test_bad_token_reports_auth_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        path = _write_credentials(tmp_path)
        bad = dataclasses.replace(fb.load_credentials(path), auth_token="wrong")
        _patch_client(monkeypatch, SESSION_PAYLOAD)
        status = fb.verify_connection(bad)
        assert status.connected is False
        assert status.error and "Authentication failed" in status.error

    def test_session_endpoint_failure_stays_connected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_client(monkeypatch, None)
        creds = fb.load_credentials(_write_credentials(tmp_path))
        status = fb.verify_connection(creds)
        assert status.connected is True
        assert status.access_tier is None

    def test_no_session_fetch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _patch_client(monkeypatch, SESSION_PAYLOAD)
        creds = fb.load_credentials(_write_credentials(tmp_path))
        status = fb.verify_connection(creds, fetch_session=False)
        assert status.connected is True
        assert status.access_tier is None

    def test_token_never_in_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _patch_client(monkeypatch, SESSION_PAYLOAD)
        creds = fb.load_credentials(_write_credentials(tmp_path))
        status = fb.verify_connection(creds)
        assert "token-abc123" not in json.dumps(dataclasses.asdict(status))


class TestCheckConnection:
    def test_end_to_end_ok(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        path = _write_credentials(tmp_path)
        monkeypatch.setattr(fb, "credentials_path", lambda: path)
        _patch_client(monkeypatch, SESSION_PAYLOAD)
        status = fb.check_connection()
        assert status.connected is True
        assert status.freebucks_balance == 10

    def test_missing_credentials_no_raise(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(fb, "credentials_path", lambda: tmp_path / "none.json")
        status = fb.check_connection()
        assert status.connected is False
        assert status.error and "No Freebuff credentials" in status.error

    def test_network_error_no_raise(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        path = _write_credentials(tmp_path)
        monkeypatch.setattr(fb, "credentials_path", lambda: path)

        class FailingClient:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                raise httpx.ConnectError("unreachable")

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(fb.httpx, "Client", FailingClient)
        status = fb.check_connection()
        assert status.connected is False
        assert status.error and "Could not reach" in status.error
