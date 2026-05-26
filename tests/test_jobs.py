from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from opencode_skill.jobs import read_prompt, submit_job


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

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


def test_read_prompt_requires_exactly_one_source(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("from file", encoding="utf-8")

    assert read_prompt(prompt="inline") == "inline"
    assert read_prompt(prompt_file=prompt_file) == "from file"
    with pytest.raises(ValueError):
        read_prompt()
    with pytest.raises(ValueError):
        read_prompt(prompt="inline", prompt_file=prompt_file)


def test_submit_job_preserves_session_by_default() -> None:
    client = FakeClient()
    result = submit_job(client, title="Synthetic Job", prompt="Do work", model="provider/model")

    assert result.session_id == "ses_job"
    assert result.status == "completed"
    assert result.deleted is False
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
