from __future__ import annotations

import base64
import os
from typing import Any

import pytest

from opencode_skill.client import OpenCodeClient, OpenCodeConfigError, OpenCodeHTTPError, infer_provider


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None, text: str | None = None) -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = text if text is not None else "{}"

    def json(self) -> Any:
        return self.payload


class FakeHTTPSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.responses: list[FakeResponse] = []

    def queue(self, response: FakeResponse) -> None:
        self.responses.append(response)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    def delete(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("DELETE", url, kwargs))
        return self.responses.pop(0)


def test_basic_auth_header_uses_supplied_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCODE_PASSWORD", raising=False)
    http = FakeHTTPSession()
    client = OpenCodeClient(base_url="http://example.test", username="user", password="secret", session=http, load_env=False)

    expected = base64.b64encode(b"user:secret").decode("ascii")
    assert client.headers == {"Authorization": f"Basic {expected}"}


def test_missing_password_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCODE_PASSWORD", raising=False)
    with pytest.raises(OpenCodeConfigError):
        OpenCodeClient(session=FakeHTTPSession(), load_env=False)


def test_create_session_and_send_message_payload() -> None:
    http = FakeHTTPSession()
    http.queue(FakeResponse(payload={"id": "ses_test"}, text='{"id":"ses_test"}'))
    http.queue(FakeResponse(payload={"ok": True}, text='{"ok":true}'))
    client = OpenCodeClient(base_url="http://example.test", username="user", password="secret", session=http, load_env=False)

    session_id = client.create_session("Synthetic Title")
    result = client.send_message(session_id, "Do the thing", model_id="provider/model", agent="agent-name")

    assert session_id == "ses_test"
    assert result == {"ok": True}
    assert http.calls[0][0:2] == ("POST", "http://example.test/session")
    message_payload = http.calls[1][2]["json"]
    assert message_payload["model"] == {"modelID": "model", "providerID": "provider"}
    assert message_payload["parts"] == [{"type": "text", "text": "Do the thing"}]
    assert message_payload["agent"] == "agent-name"


def test_http_error_body_is_truncated_and_credential_free() -> None:
    http = FakeHTTPSession()
    http.queue(FakeResponse(status_code=500, payload={}, text="server failed"))
    client = OpenCodeClient(base_url="http://example.test", username="user", password="secret", session=http, load_env=False)

    with pytest.raises(OpenCodeHTTPError) as excinfo:
        client.list_sessions()
    message = str(excinfo.value)
    assert "server failed" in message
    assert "secret" not in message


def test_wait_for_session_complete_polls_until_idle() -> None:
    http = FakeHTTPSession()
    http.queue(FakeResponse(payload={"ses_test": {"type": "busy"}}, text='{"ses_test":{"type":"busy"}}'))
    http.queue(FakeResponse(payload={}, text="{}"))
    sleeps: list[float] = []
    client = OpenCodeClient(base_url="http://example.test", username="user", password="secret", session=http, load_env=False, sleep=sleeps.append)

    assert client.wait_for_session_complete("ses_test", poll_interval=0.25, max_wait=5)
    assert sleeps == [0.25]
    assert [call[1] for call in http.calls] == [
        "http://example.test/session/status",
        "http://example.test/session/status",
    ]


def test_wait_for_session_complete_requires_two_idle_polls_before_seen_busy() -> None:
    http = FakeHTTPSession()
    http.queue(FakeResponse(payload={}, text="{}"))
    http.queue(FakeResponse(payload={}, text="{}"))
    sleeps: list[float] = []
    client = OpenCodeClient(
        base_url="http://example.test",
        username="user",
        password="secret",
        session=http,
        load_env=False,
        sleep=sleeps.append,
    )

    assert client.wait_for_session_complete("ses_fast", poll_interval=0.25, max_wait=5)
    assert sleeps == [0.25]


def test_provider_inference() -> None:
    assert infer_provider("provider/model") == ("provider", "model")
    assert infer_provider("model", "explicit") == ("explicit", "model")
    old = os.environ.get("OPENCODE_PROVIDER")
    os.environ["OPENCODE_PROVIDER"] = "env-provider"
    try:
        assert infer_provider("model") == ("env-provider", "model")
    finally:
        if old is None:
            os.environ.pop("OPENCODE_PROVIDER", None)
        else:
            os.environ["OPENCODE_PROVIDER"] = old
