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

"""Freebuff (codebuff.com) account connector.

Reads the locally persisted Freebuff CLI credentials, verifies them against
the live ``https://www.codebuff.com`` backend, and reports account/session
status (tier, Freebucks balance, country gating).

This connector is intentionally read-only and never sends prompts anywhere:
the backend exposes account/session management endpoints only, and model
access rides the CLI's proprietary websocket protocol. LLM calls inside
Artemis therefore keep using their own provider keys; this module exists so
operators can verify the machine's Freebuff identity and quota in one place.

The auth token is treated as a secret: it is never logged and never included
in returned status payloads.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from artemis.utils.logger import get_logger

logger = get_logger(__name__)

#: The Freebuff CLI persists login state here (binary is ``freebuff``,
#: config dir is the historical ``manicode`` path).
DEFAULT_CREDENTIALS_PATH = Path.home() / ".config" / "manicode" / "credentials.json"
#: ``FREEBUFF_CREDENTIALS_PATH`` overrides where the connector looks.
CREDENTIALS_PATH_ENV = "FREEBUFF_CREDENTIALS_PATH"

#: The Freebuff web backend serving the REST API consumed by the CLI.
BACKEND_URL = "https://www.codebuff.com"

#: Timeout for the live verification requests, in seconds.
DEFAULT_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class FreebuffCredentials:
    """Account identity stored by the Freebuff CLI."""

    user_id: str
    email: str
    display_name: str
    auth_token: str
    source_path: Path


@dataclass(frozen=True)
class FreebuffStatus:
    """Result of verifying the local Freebuff identity against the backend."""

    connected: bool
    email: str | None = None
    user_id: str | None = None
    backend_url: str = BACKEND_URL
    error: str | None = None
    access_tier: str | None = None
    session_status: str | None = None
    country_code: str | None = None
    country_block_reason: str | None = None
    freebucks_balance: int | None = None
    freebucks_daily_remaining: int | None = None
    freebucks_daily_limit: int | None = None
    freebucks_daily_reset_at: str | None = None
    #: Model access still requires per-provider keys; freebuff credentials
    #: cannot authenticate LLM calls. Kept as an explicit field so callers
    #: surface the limitation instead of guessing.
    llm_routing_available: bool = field(default=False, repr=False)


def credentials_path() -> Path:
    """Resolve the credentials file path (env override > default)."""

    override = os.environ.get(CREDENTIALS_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_CREDENTIALS_PATH


def load_credentials(path: Path | None = None) -> FreebuffCredentials:
    """Load and validate the locally persisted Freebuff CLI credentials.

    Args:
        path: Explicit credentials file; defaults to :func:`credentials_path`.

    Returns:
        The parsed identity.

    Raises:
        FileNotFoundError: The credentials file does not exist.
        ValueError: The file is not valid JSON or lacks the expected shape.
    """

    resolved = (path or credentials_path()).expanduser()
    if not resolved.is_file():
        raise FileNotFoundError(
            f"No Freebuff credentials found at {resolved}. "
            "Run the freebuff CLI and log in to create them."
        )

    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Credentials file {resolved} is unreadable: {exc}") from exc

    # The CLI nests the active profile under a "default" key; tolerate a flat
    # layout as a fallback so both shapes are supported.
    profile = payload.get("default") if isinstance(payload, dict) else None
    if not isinstance(profile, dict):
        profile = payload if isinstance(payload, dict) else {}

    auth_token = str(profile.get("authToken") or "").strip()
    if not auth_token:
        raise ValueError(f"Credentials file {resolved} has no authToken.")

    return FreebuffCredentials(
        user_id=str(profile.get("id") or "").strip(),
        email=str(profile.get("email") or "").strip(),
        display_name=str(profile.get("name") or "").strip(),
        auth_token=auth_token,
        source_path=resolved,
    )


def _fields_error(resp: httpx.Response) -> str:
    """Best-effort extraction of the backend's error message."""

    try:
        data = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}"
    if isinstance(data, dict):
        for key in ("error", "message", "detail"):
            value = data.get(key)
            if value:
                return str(value)
    return f"HTTP {resp.status_code}"


def _apply_session_payload(status: FreebuffStatus, payload: dict[str, Any]) -> FreebuffStatus:
    """Copy session/account quota fields from a /freebuff/session payload."""

    freebucks = payload.get("freebucks") or {}
    daily = freebucks.get("daily") or {}
    return FreebuffStatus(
        connected=status.connected,
        email=status.email,
        user_id=status.user_id,
        backend_url=status.backend_url,
        error=status.error,
        access_tier=_optional_str(payload.get("accessTier")),
        session_status=_optional_str(payload.get("status")),
        country_code=_optional_str(payload.get("countryCode")),
        country_block_reason=_optional_str(payload.get("countryBlockReason")),
        freebucks_balance=_optional_int(freebucks.get("balance")),
        freebucks_daily_remaining=_optional_int(daily.get("remaining")),
        freebucks_daily_limit=_optional_int(daily.get("limit")),
        freebucks_daily_reset_at=_optional_str(daily.get("resetAt")),
        llm_routing_available=status.llm_routing_available,
    )


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def verify_connection(
    credentials: FreebuffCredentials,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    fetch_session: bool = True,
) -> FreebuffStatus:
    """Verify the credentials against the live backend and gather status.

    Makes at most two GET requests (``/api/v1/me`` and, when enabled,
    ``/api/v1/freebuff/session``). Network or auth failures yield a status
    with ``connected=False`` and a human-readable ``error``; this function
    never raises for remote conditions, only for local programming errors.

    Args:
        credentials: Credentials loaded via :func:`load_credentials`.
        timeout: Per-request timeout in seconds.
        fetch_session: Also fetch session/quota information.

    Returns:
        A :class:`FreebuffStatus` snapshot.
    """

    headers = {"Authorization": f"Bearer {credentials.auth_token}"}

    with httpx.Client(timeout=timeout, headers=headers) as client:
        me_resp = client.get(f"{BACKEND_URL}/api/v1/me", params={"fields": "id,email"})
        if me_resp.status_code != 200:
            return FreebuffStatus(
                connected=False,
                error=f"Authentication failed: {_fields_error(me_resp)}",
            )

        me_payload = me_resp.json()
        status = FreebuffStatus(
            connected=True,
            email=_optional_str(me_payload.get("email")) or credentials.email or None,
            user_id=_optional_str(me_payload.get("id")) or credentials.user_id or None,
        )

        if not fetch_session:
            return status

        try:
            session_resp = client.get(f"{BACKEND_URL}/api/v1/freebuff/session")
        except httpx.HTTPError as exc:
            logger.debug(f"Session fetch failed after successful auth: {exc}")
            return status

        if session_resp.status_code != 200:
            return status

        try:
            session_payload = session_resp.json()
        except ValueError:
            return status

        if isinstance(session_payload, dict):
            return _apply_session_payload(status, session_payload)
        return status


def check_connection(
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    path: Path | None = None,
) -> FreebuffStatus:
    """One-shot helper: load local credentials and verify them live.

    Never raises for missing credentials or network problems; the returned
    status carries ``connected=False`` plus an ``error`` message instead.
    """

    try:
        credentials = load_credentials(path)
    except (FileNotFoundError, ValueError) as exc:
        return FreebuffStatus(connected=False, error=str(exc))

    try:
        return verify_connection(credentials, timeout=timeout)
    except httpx.HTTPError as exc:
        return FreebuffStatus(
            connected=False,
            email=credentials.email or None,
            error=f"Could not reach {BACKEND_URL}: {exc}",
        )
