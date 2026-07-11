from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import requests


DEFAULT_BASE_URL = "http://localhost:4096"
DEFAULT_USERNAME = "opencode"
DEFAULT_MESSAGE_TIMEOUT_SECONDS = 3600.0


class ResponseLike(Protocol):
    status_code: int
    text: str

    def json(self) -> Any: ...


class SessionLike(Protocol):
    def get(self, url: str, **kwargs: Any) -> ResponseLike: ...
    def post(self, url: str, **kwargs: Any) -> ResponseLike: ...
    def delete(self, url: str, **kwargs: Any) -> ResponseLike: ...


class OpenCodeError(RuntimeError):
    """Base exception for OpenCode HTTP client failures."""


class OpenCodeConfigError(OpenCodeError):
    """Raised when required client configuration is missing."""


@dataclass(frozen=True)
class OpenCodeHTTPError(OpenCodeError):
    method: str
    url: str
    status_code: int
    body: str

    def __str__(self) -> str:
        body = f": {self.body}" if self.body else ""
        return f"{self.method} {self.url} returned HTTP {self.status_code}{body}"


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or Path.cwd() / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    return float(value)


def infer_provider(model: str, provider: str | None = None) -> tuple[str | None, str]:
    if provider:
        return provider, model
    if "/" in model:
        inferred_provider, model_id = model.split("/", 1)
        return inferred_provider, model_id
    env_provider = os.environ.get("OPENCODE_PROVIDER") or os.environ.get("OPENCODE_DEFAULT_PROVIDER")
    return env_provider, model


def _response_body(response: ResponseLike, limit: int = 500) -> str:
    text = getattr(response, "text", "") or ""
    return text[:limit]


def _raise_for_status(method: str, url: str, response: ResponseLike) -> None:
    if 200 <= response.status_code < 300:
        return
    raise OpenCodeHTTPError(method=method, url=url, status_code=response.status_code, body=_response_body(response))


def _json_response(method: str, url: str, response: ResponseLike, *, allow_empty: bool = False) -> Any:
    _raise_for_status(method, url, response)
    if not (getattr(response, "text", "") or "").strip():
        if allow_empty:
            return None
        raise OpenCodeHTTPError(method=method, url=url, status_code=response.status_code, body="empty response body")
    return response.json()


class OpenCodeClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        session: Any | None = None,
        load_env: bool = True,
        message_timeout: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if load_env:
            load_dotenv()
        self.base_url = (base_url or os.environ.get("OPENCODE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.username = username or os.environ.get("OPENCODE_USERNAME") or DEFAULT_USERNAME
        self.password = password if password is not None else os.environ.get("OPENCODE_PASSWORD")
        if not self.password:
            raise OpenCodeConfigError("OPENCODE_PASSWORD is required for OpenCode HTTP API authentication")
        self.message_timeout = message_timeout if message_timeout is not None else env_float(
            "OPENCODE_MESSAGE_TIMEOUT", DEFAULT_MESSAGE_TIMEOUT_SECONDS
        )
        self._session: Any = session or requests.Session()
        self._sleep = sleep
        credentials = f"{self.username}:{self.password}".encode("utf-8")
        self.headers = {"Authorization": "Basic " + base64.b64encode(credentials).decode("ascii")}

    def list_sessions(self) -> list[dict[str, Any]]:
        url = f"{self.base_url}/session"
        response = self._session.get(url, headers=self.headers, timeout=30)
        payload = _json_response("GET", url, response)
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("sessions"), list):
            return payload["sessions"]
        return []

    def create_session(self, title: str) -> str:
        url = f"{self.base_url}/session"
        response = self._session.post(url, json={"title": title}, headers=self.headers, timeout=30)
        payload = _json_response("POST", url, response)
        session_id = self._extract_session_id(payload)
        if not session_id:
            raise OpenCodeError("OpenCode create session response did not include a session id")
        return session_id

    def send_message(
        self,
        session_id: str,
        message: str,
        *,
        model_id: str,
        provider_id: str | None = None,
        agent: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        provider, model = infer_provider(model_id, provider_id)
        model_payload: dict[str, str] = {"modelID": model}
        if provider:
            model_payload["providerID"] = provider
        payload: dict[str, Any] = {
            "parts": [{"type": "text", "text": message}],
            "model": model_payload,
        }
        if agent:
            payload["agent"] = agent
        url = f"{self.base_url}/session/{session_id}/message"
        response = self._session.post(
            url,
            json=payload,
            headers=self.headers,
            timeout=self.message_timeout if timeout is None else timeout,
        )
        parsed = _json_response("POST", url, response, allow_empty=True)
        if parsed is not None:
            return parsed
        if self._wait_for_first_assistant_message(session_id):
            return {"status": "accepted_empty_response", "session_id": session_id}
        return {"status": "accepted_empty_response_unconfirmed", "session_id": session_id}

    def get_session_info(self, session_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/session/{session_id}"
        response = self._session.get(url, headers=self.headers, timeout=10)
        payload = _json_response("GET", url, response)
        return payload if isinstance(payload, dict) else {}

    def get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/session/{session_id}/message"
        response = self._session.get(url, headers=self.headers, timeout=10)
        payload = _json_response("GET", url, response)
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
            return payload["messages"]
        return []

    def get_session_statuses(self) -> dict[str, dict[str, Any]]:
        """Return the authoritative busy/idle status map for active sessions."""
        url = f"{self.base_url}/session/status"
        response = self._session.get(url, headers=self.headers, timeout=10)
        payload = _json_response("GET", url, response)
        return payload if isinstance(payload, dict) else {}

    def delete_session(self, session_id: str) -> bool:
        url = f"{self.base_url}/session/{session_id}"
        response = self._session.delete(url, headers=self.headers, timeout=10)
        _raise_for_status("DELETE", url, response)
        return True

    def wait_for_session_complete(
        self,
        session_id: str,
        *,
        poll_interval: float = 15.0,
        max_wait: float = 7200.0,
    ) -> bool:
        started = time.monotonic()
        seen_active = False
        idle_polls = 0
        while time.monotonic() - started < max_wait:
            status = self.get_session_statuses().get(session_id) or {}
            status_type = str(status.get("type") or "idle").lower()
            if status_type in {"busy", "queued", "pending", "running", "retry"}:
                seen_active = True
                idle_polls = 0
            else:
                idle_polls += 1
                if seen_active or idle_polls >= 2:
                    return True
            self._sleep(poll_interval)
        return False

    def _wait_for_first_assistant_message(
        self,
        session_id: str,
        *,
        max_wait: float = 45.0,
        poll_interval: float = 3.0,
    ) -> bool:
        started = time.monotonic()
        while time.monotonic() - started < max_wait:
            messages = self.get_session_messages(session_id)
            if any((message.get("info") or message).get("role") == "assistant" for message in messages):
                return True
            self._sleep(poll_interval)
        return False

    @staticmethod
    def _extract_session_id(payload: Any) -> str | None:
        if isinstance(payload, str):
            return payload
        if not isinstance(payload, dict):
            return None
        for key in ("id", "session_id", "sessionID"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        for key in ("session", "data"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                for nested_key in ("id", "session_id", "sessionID"):
                    value = nested.get(nested_key)
                    if isinstance(value, str):
                        return value
        return None

    @staticmethod
    def _session_is_running(info: dict[str, Any]) -> bool:
        if bool(info.get("running") or info.get("busy")):
            return True
        status = str(info.get("status") or "").lower()
        return status in {"running", "busy", "queued", "pending"}
