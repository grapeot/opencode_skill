from __future__ import annotations

import base64
from typing import Any

import pytest

from opencode_skill.client import (
    ModelRefError,
    OpenCodeClient,
    OpenCodeConfigError,
    OpenCodeHTTPError,
    cli_model_provider,
    infer_provider,
    resolve_model_ref,
)


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


def test_send_message_async_posts_to_prompt_async_and_returns_none() -> None:
    http = FakeHTTPSession()
    http.queue(FakeResponse(status_code=204, payload=None, text=""))
    client = OpenCodeClient(base_url="http://example.test", username="user", password="secret", session=http, load_env=False)

    result = client.send_message_async("ses_test", "Do the thing", model_id="provider/model", agent="agent-name")

    assert result is None
    method, url, kwargs = http.calls[0]
    assert (method, url) == ("POST", "http://example.test/session/ses_test/prompt_async")
    assert kwargs["json"]["model"] == {"modelID": "model", "providerID": "provider"}
    assert kwargs["json"]["parts"] == [{"type": "text", "text": "Do the thing"}]


def test_send_message_async_raises_on_rejected_handoff() -> None:
    http = FakeHTTPSession()
    http.queue(FakeResponse(status_code=404, payload={}, text='{"name":"NotFoundError"}'))
    client = OpenCodeClient(base_url="http://example.test", username="user", password="secret", session=http, load_env=False)

    with pytest.raises(OpenCodeHTTPError) as excinfo:
        client.send_message_async("ses_missing", "Do the thing", model_id="provider/model")
    assert excinfo.value.status_code == 404


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
    with pytest.raises(ModelRefError, match="same source"):
        infer_provider("model")


def test_provider_inference_keeps_slashes_in_model_id() -> None:
    # Hugging Face ids are "org/name"; the first slash is the provider, the
    # rest belongs to the model.
    assert infer_provider("huggingface/example-org/example-model") == (
        "huggingface",
        "example-org/example-model",
    )
    assert infer_provider("example-org/example-model", "huggingface") == (
        "huggingface",
        "example-org/example-model",
    )


def test_resolve_model_ref_is_idempotent() -> None:
    # submit_job resolves the ref into (provider, model), then send_message
    # resolves that pair again as (model, provider). Re-resolving must be a
    # no-op even when the model id itself contains further slashes.
    provider, model = resolve_model_ref("huggingface/example-org/example-model")
    assert resolve_model_ref(model, provider) == (provider, model)


def test_resolve_model_ref_is_idempotent_when_org_matches_provider() -> None:
    # A model id whose org segment equals the provider (e.g. "huggingface/...")
    # must survive re-resolution unchanged; an explicit provider is literal.
    provider, model = resolve_model_ref("huggingface/huggingface/model-name")
    assert (provider, model) == ("huggingface", "huggingface/model-name")
    assert resolve_model_ref(model, provider) == (provider, model)
    assert resolve_model_ref("huggingface/model-name", "huggingface") == (
        "huggingface",
        "huggingface/model-name",
    )


def test_resolve_model_ref_explicit_provider_is_literal_on_mismatch() -> None:
    # With an explicit provider the model string is literal; resolution does
    # not guess that a leading "other/" is a duplicate provider.
    assert resolve_model_ref("openai/gpt-4", "anthropic") == ("anthropic", "openai/gpt-4")


def test_list_sessions_passes_only_known_query_params() -> None:
    http = FakeHTTPSession()
    http.queue(FakeResponse(payload=[], text="[]"))
    client = OpenCodeClient(base_url="http://example.test", username="user", password="secret", session=http, load_env=False)

    assert client.list_sessions(directory="/work/alpha", roots=True, search="alpha", limit=8) == []
    params = http.calls[0][2]["params"]
    assert params == {"directory": "/work/alpha", "roots": "true", "search": "alpha", "limit": "8"}
    with pytest.raises(ValueError, match="scope"):
        client.list_sessions(scope="global")


def test_children_status_and_bounded_experimental_reads() -> None:
    http = FakeHTTPSession()
    http.queue(FakeResponse(payload=[{"id": "ses_example_child"}], text='[{"id":"ses_example_child"}]'))
    http.queue(FakeResponse(payload={"ses_example_current": {"type": "busy"}}, text="{}"))
    http.queue(FakeResponse(payload=[], text="[]"))
    client = OpenCodeClient(base_url="http://example.test", username="user", password="secret", session=http, load_env=False)

    assert client.list_children("ses_example_parent", directory="/work/alpha") == [{"id": "ses_example_child"}]
    assert client.get_session_statuses(directory="/work/alpha")["ses_example_current"]["type"] == "busy"
    assert client.list_experimental_sessions(directory="/work/alpha", limit=5, archived=True) == []
    assert http.calls[0][1] == "http://example.test/session/ses_example_parent/children"
    assert http.calls[0][2]["params"] == {"directory": "/work/alpha"}
    assert "workspace" not in http.calls[0][2]["params"]
    assert http.calls[1][2]["params"] == {"directory": "/work/alpha"}
    assert "workspace" not in http.calls[1][2]["params"]
    assert http.calls[2][1] == "http://example.test/experimental/session"
    assert http.calls[2][2]["params"]["archived"] == "true"
    assert http.calls[2][2]["params"]["limit"] == "5"
    with pytest.raises(ValueError, match="directory or search"):
        client.list_experimental_sessions(limit=5)


def test_cli_model_provider_does_not_mix_env_provider_with_cli_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCODE_MODEL", "env-model")
    monkeypatch.setenv("OPENCODE_PROVIDER", "env-provider")
    assert cli_model_provider(None, None) == ("env-model", "env-provider")
    assert cli_model_provider("provider/model", None) == ("provider/model", None)
    assert resolve_model_ref(*cli_model_provider("provider/model", None)) == ("provider", "model")
    with pytest.raises(ModelRefError, match="same source"):
        resolve_model_ref(*cli_model_provider("bare-model", None))
