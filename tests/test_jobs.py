from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
import requests

from opencode_skill.jobs import (
    DRY_RUN_PROMPT,
    DryRunVerificationError,
    append_job,
    read_prompt,
    session_archive_job,
    submit_dry_run,
    submit_job,
)


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

    def send_message_async(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("send_message_async", args, kwargs))

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
    assert [call[0] for call in client.calls] == ["create_session", "send_message_async"]


class ArchiveFakeClient:
    def __init__(self, state: dict[str, int] | None = None) -> None:
        self.calls: list[tuple[str, Any, Any]] = []
        self.state: dict[str, int] = dict(state or {})

    def get_session_info(self, session_id: str) -> dict[str, Any]:
        self.calls.append(("get_session_info", session_id, None))
        if session_id not in self.state:
            return {}
        return {"id": session_id, "time": {"archived": self.state[session_id]}}

    def set_session_archived(self, session_id: str, *, archived_ms: int) -> dict[str, Any]:
        self.calls.append(("set_session_archived", session_id, archived_ms))
        self.state[session_id] = archived_ms
        return {"id": session_id, "time": {"archived": archived_ms}}


def test_session_archive_job_unarchives_with_zero_and_verifies() -> None:
    client = ArchiveFakeClient({"ses_one": 12})

    result = session_archive_job(client, session_id="ses_one", unarchive=True)

    assert result.mode == "unarchive"
    assert result.previous_archived == 12
    assert result.archived == 0
    assert result.status == "verified"
    assert [call[0] for call in client.calls] == ["get_session_info", "set_session_archived"]
    assert client.calls[1][2] == 0


def test_session_archive_job_stamps_current_time_on_archive() -> None:
    client = ArchiveFakeClient()
    before = int(time.time() * 1000)

    result = session_archive_job(client, session_id="ses_two", unarchive=False)

    assert result.mode == "archive"
    assert result.previous_archived is None
    assert result.archived is not None and result.archived >= before
    assert result.status == "verified"


def test_session_archive_job_dry_run_reports_state_and_mutates_nothing() -> None:
    client = ArchiveFakeClient({"ses_three": 5})

    result = session_archive_job(client, session_id="ses_three", unarchive=False, dry_run=True)

    assert result.mode == "archive"
    assert result.previous_archived == 5
    assert result.archived is None
    assert result.status == "dry_run"
    assert [call[0] for call in client.calls] == ["get_session_info"]


def test_session_archive_job_reports_failed_post_patch_when_echo_is_stale() -> None:
    class StaleClient:
        def get_session_info(self, _session_id):
            return {"id": "ses_four", "time": {"archived": 7}}

        def set_session_archived(self, _session_id, *, archived_ms):
            return {"id": "ses_four", "time": {"archived": 7}}

    result = session_archive_job(StaleClient(), session_id="ses_four", unarchive=True)

    assert result.status == "failed_post_patch"
    assert result.error


def test_session_archive_job_reports_failed_post_patch_for_nonconfirming_echoes() -> None:
    class EchoingClient:
        def __init__(self, echo: Any) -> None:
            self.echo = echo

        def get_session_info(self, _session_id):
            return {}

        def set_session_archived(self, _session_id, *, archived_ms):
            return self.echo

    nonconfirming = [
        {"time": {"archived": 7.5}},
        {"time": {"archived": True}},
        {"time": {"archived": -1}},
        {"time": {"archived": "17"}},
        {},
    ]
    for echo in nonconfirming:
        unarchive_result = session_archive_job(EchoingClient(echo), session_id="ses_five", unarchive=True)
        archive_result = session_archive_job(EchoingClient(echo), session_id="ses_five", unarchive=False)
        assert unarchive_result.status == "failed_post_patch"
        assert archive_result.status == "failed_post_patch"

    missing_field = {"time": {"created": 1, "updated": 2}}
    archive_result = session_archive_job(EchoingClient(missing_field), session_id="ses_five", unarchive=False)
    unarchive_result = session_archive_job(EchoingClient(missing_field), session_id="ses_five", unarchive=True)
    assert archive_result.status == "failed_post_patch"
    assert unarchive_result.status == "verified"


def test_session_archive_job_classifies_transport_failures_by_phase() -> None:
    class PrePatchClient:
        def get_session_info(self, _session_id):
            raise requests.exceptions.ConnectionError("connection refused")

        def set_session_archived(self, _session_id, *, archived_ms):
            raise AssertionError("must not be called")

    class PostPatchClient:
        def __init__(self) -> None:
            self.patches: list[tuple[str, int]] = []

        def get_session_info(self, _session_id):
            return {}

        def set_session_archived(self, session_id, *, archived_ms):
            self.patches.append((session_id, archived_ms))
            raise requests.exceptions.ConnectionError("connection refused")

    pre = session_archive_job(PrePatchClient(), session_id="ses_pre", unarchive=True)
    post_client = PostPatchClient()
    post = session_archive_job(post_client, session_id="ses_post", unarchive=True)

    assert pre.status == "failed_pre_patch"
    assert pre.previous_archived is None
    assert post.status == "failed_post_patch"
    assert post.previous_archived is None
    assert len(post_client.patches) == 1


def test_session_archive_job_accepts_absent_archived_field_for_unarchive() -> None:
    class AbsentClient:
        def get_session_info(self, _session_id):
            return {}

        def set_session_archived(self, _session_id, *, archived_ms):
            return {"id": "ses_six", "time": {"created": 1, "updated": 2}}

    result = session_archive_job(AbsentClient(), session_id="ses_six", unarchive=True)

    assert result.status == "verified"
    assert result.archived is None


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
    assert [call[0] for call in client.calls] == ["create_session", "send_message_async", "delete_session"]
    send_kwargs = client.calls[1][2]
    assert send_kwargs["model_id"] == "model"
    assert send_kwargs["provider_id"] == "provider"


def test_submit_job_propagates_handoff_failure() -> None:
    class FailingClient(FakeClient):
        def send_message_async(self, *args: Any, **kwargs: Any) -> None:
            self.calls.append(("send_message_async", args, kwargs))
            raise RuntimeError("server rejected the prompt")

    client = FailingClient()
    with pytest.raises(RuntimeError, match="server rejected the prompt"):
        submit_job(client, title="Synthetic Job", prompt="Do work", model="provider/model")


def test_append_job_sends_to_existing_session_without_creating() -> None:
    client = FakeClient()
    result = append_job(client, session_id="ses_existing", prompt="Follow up", model="provider/model")

    assert result.session_id == "ses_existing"
    assert result.status == "submitted"
    assert result.deleted is False
    assert [call[0] for call in client.calls] == ["send_message_async"]
    assert client.calls[0][1][0] == "ses_existing"


def test_append_job_can_wait_for_existing_session_completion() -> None:
    client = FakeClient()
    result = append_job(client, session_id="ses_existing", prompt="Follow up", model="provider/model", wait=True)

    assert result.session_id == "ses_existing"
    assert result.status == "completed"
    assert result.wait_completed is True
    assert [call[0] for call in client.calls] == ["send_message", "wait_for_session_complete"]


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
