from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from opencode_skill.jobs import DRY_RUN_PROMPT, DryRunVerificationError, read_prompt, submit_dry_run, submit_job


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.messages: list[dict[str, Any]] = [{"info": {"role": "assistant"}, "parts": [{"text": "OK"}]}]

    def create_session(self, title: str) -> str:
        self.calls.append(("create_session", (title,), {}))
        return "ses_job"

    def send_message(self, *args: Any, **kwargs: Any) -> dict[str, bool]:
        self.calls.append(("send_message", args, kwargs))
        return {"ok": True}

    def wait_for_session_complete(self, *args: Any, **kwargs: Any) -> bool:
        self.calls.append(("wait_for_session_complete", args, kwargs))
        return True

    def delete_session(self, *args: Any, **kwargs: Any) -> bool:
        self.calls.append(("delete_session", args, kwargs))
        return True

    def get_session_messages(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("get_session_messages", args, kwargs))
        return self.messages


def test_read_prompt_requires_exactly_one_source(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("from file", encoding="utf-8")

    assert read_prompt(prompt="inline") == "inline"
    assert read_prompt(prompt_file=prompt_file) == "from file"
    with pytest.raises(ValueError):
        read_prompt()
    with pytest.raises(ValueError):
        read_prompt(prompt="inline", prompt_file=prompt_file)


def test_submit_job_hands_off_by_default() -> None:
    client = FakeClient()
    result = submit_job(client, title="Synthetic Job", prompt="Do work", model="provider/model")

    assert result.session_id == "ses_job"
    assert result.status == "submitted"
    assert result.deleted is False
    assert [call[0] for call in client.calls] == ["create_session", "send_message"]


def test_submit_job_can_wait_for_completion() -> None:
    client = FakeClient()
    result = submit_job(client, title="Synthetic Job", prompt="Do work", model="provider/model", wait=True)

    assert result.session_id == "ses_job"
    assert result.status == "completed"
    assert result.wait_completed is True
    assert [call[0] for call in client.calls] == ["create_session", "send_message", "wait_for_session_complete"]


def test_submit_job_can_skip_wait_and_delete_session() -> None:
    client = FakeClient()
    result = submit_job(
        client,
        title="Synthetic Job",
        prompt="Do work",
        model="model",
        provider="provider",
        wait=False,
        delete_session=True,
    )

    assert result.status == "submitted_deleted"
    assert result.deleted is True
    assert [call[0] for call in client.calls] == ["create_session", "send_message", "delete_session"]
    send_kwargs = client.calls[1][2]
    assert send_kwargs["model_id"] == "model"
    assert send_kwargs["provider_id"] == "provider"


def test_submit_job_timeout_in_handoff_returns_session() -> None:
    class SlowClient(FakeClient):
        def send_message(self, *args: Any, **kwargs: Any) -> dict[str, bool]:
            import time

            self.calls.append(("send_message", args, kwargs))
            time.sleep(0.1)
            return {"ok": True}

    client = SlowClient()
    result = submit_job(client, title="Synthetic Job", prompt="Do work", model="provider/model", send_timeout=0.01)

    assert result.session_id == "ses_job"
    assert result.status == "submitted_timeout"
    assert result.wait_completed is None


def test_submit_dry_run_sends_only_harmless_prompt_and_deletes_session() -> None:
    client = FakeClient()
    result = submit_dry_run(client, title="Synthetic Job", model="provider/model")

    assert result.status == "dry_run_ok_deleted"
    assert result.dry_run is True
    assert result.verification == "assistant_replied_ok"
    assert result.deleted is True
    assert [call[0] for call in client.calls] == [
        "create_session",
        "send_message",
        "wait_for_session_complete",
        "get_session_messages",
        "delete_session",
    ]
    assert client.calls[0][1] == ("[dry-run] Synthetic Job",)
    assert client.calls[1][1][1] == DRY_RUN_PROMPT


def test_submit_dry_run_fails_when_assistant_does_not_reply_ok() -> None:
    client = FakeClient()
    client.messages = [{"info": {"role": "assistant"}, "parts": [{"text": "Not OK"}]}]

    with pytest.raises(DryRunVerificationError):
        submit_dry_run(client, title="Synthetic Job", model="provider/model")

    assert client.calls[-1][0] == "delete_session"
