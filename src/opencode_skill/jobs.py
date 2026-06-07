from __future__ import annotations

import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from .client import infer_provider


@dataclass(frozen=True)
class JobSubmissionResult:
    session_id: str
    title: str
    status: str
    deleted: bool
    wait_completed: bool | None
    dry_run: bool = False
    verification: str | None = None


DRY_RUN_PROMPT = """This is an OpenCode submission dry run.

Do not inspect files, call tools, modify state, or perform the user's real task.
Reply with exactly this two-letter string and nothing else:

OK
"""


class DryRunVerificationError(RuntimeError):
    """Raised when an OpenCode dry run does not produce the expected response."""


def read_prompt(
    *,
    prompt: str | None = None,
    prompt_file: Path | None = None,
    stdin: TextIO | None = None,
    use_stdin: bool = False,
) -> str:
    sources = sum(1 for value in (prompt, prompt_file, use_stdin) if value)
    if sources != 1:
        raise ValueError("provide exactly one prompt source: prompt text, --prompt-file, or --stdin")
    if prompt is not None:
        return prompt
    if prompt_file is not None:
        return prompt_file.expanduser().read_text(encoding="utf-8")
    stream = stdin or sys.stdin
    return stream.read()


def submit_job(
    client: Any,
    *,
    title: str,
    prompt: str,
    model: str,
    provider: str | None = None,
    agent: str | None = None,
    wait: bool = False,
    delete_session: bool = False,
    send_timeout: float | None = None,
    wait_poll_interval: float = 15.0,
    wait_max_seconds: float = 7200.0,
) -> JobSubmissionResult:
    provider_id, model_id = infer_provider(model, provider)
    session_id = client.create_session(title)
    wait_completed = None
    if wait:
        client.send_message(
            session_id,
            prompt,
            model_id=model_id,
            provider_id=provider_id,
            agent=agent,
            timeout=send_timeout,
        )
        wait_completed = client.wait_for_session_complete(
            session_id,
            poll_interval=wait_poll_interval,
            max_wait=wait_max_seconds,
        )
        status = "completed" if wait_completed else "wait_timeout"
    else:
        status = _send_message_for_handoff(
            client,
            session_id=session_id,
            prompt=prompt,
            model_id=model_id,
            provider_id=provider_id,
            agent=agent,
            send_timeout=send_timeout,
        )
    deleted = False
    if delete_session:
        deleted = client.delete_session(session_id)
        if deleted:
            status = f"{status}_deleted"
    return JobSubmissionResult(
        session_id=session_id,
        title=title,
        status=status,
        deleted=deleted,
        wait_completed=wait_completed,
    )


def append_job(
    client: Any,
    *,
    session_id: str,
    prompt: str,
    model: str,
    provider: str | None = None,
    agent: str | None = None,
    wait: bool = False,
    send_timeout: float | None = None,
    wait_poll_interval: float = 15.0,
    wait_max_seconds: float = 7200.0,
) -> JobSubmissionResult:
    provider_id, model_id = infer_provider(model, provider)
    wait_completed = None
    if wait:
        client.send_message(
            session_id,
            prompt,
            model_id=model_id,
            provider_id=provider_id,
            agent=agent,
            timeout=send_timeout,
        )
        wait_completed = client.wait_for_session_complete(
            session_id,
            poll_interval=wait_poll_interval,
            max_wait=wait_max_seconds,
        )
        status = "completed" if wait_completed else "wait_timeout"
    else:
        status = _send_message_for_handoff(
            client,
            session_id=session_id,
            prompt=prompt,
            model_id=model_id,
            provider_id=provider_id,
            agent=agent,
            send_timeout=send_timeout,
        )
    return JobSubmissionResult(
        session_id=session_id,
        title="",
        status=status,
        deleted=False,
        wait_completed=wait_completed,
    )


def _send_message_for_handoff(
    client: Any,
    *,
    session_id: str,
    prompt: str,
    model_id: str,
    provider_id: str | None,
    agent: str | None,
    send_timeout: float | None,
) -> str:
    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def send() -> None:
        try:
            result = client.send_message(
                session_id,
                prompt,
                model_id=model_id,
                provider_id=provider_id,
                agent=agent,
                timeout=send_timeout,
            )
            result_queue.put(("result", result))
        except Exception as exc:  # noqa: BLE001 - handoff status preserves the session id for follow-up
            result_queue.put(("error", exc))

    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    thread.join(None if send_timeout is None or send_timeout <= 0 else send_timeout)

    if thread.is_alive():
        return "submitted_timeout"
    kind, result = result_queue.get() if not result_queue.empty() else ("result", None)
    if kind == "error" or result is None:
        return "submitted_unconfirmed"
    return "submitted"


def submit_dry_run(
    client: Any,
    *,
    title: str,
    model: str,
    provider: str | None = None,
    agent: str | None = None,
    delete_session: bool = True,
    send_timeout: float | None = None,
    wait_poll_interval: float = 3.0,
    wait_max_seconds: float = 120.0,
) -> JobSubmissionResult:
    provider_id, model_id = infer_provider(model, provider)
    session_id = client.create_session(f"[dry-run] {title}")
    client.send_message(
        session_id,
        DRY_RUN_PROMPT,
        model_id=model_id,
        provider_id=provider_id,
        agent=agent,
        timeout=send_timeout,
    )
    wait_completed = client.wait_for_session_complete(
        session_id,
        poll_interval=wait_poll_interval,
        max_wait=wait_max_seconds,
    )
    if not wait_completed:
        _delete_after_dry_run(client, session_id, delete_session)
        raise DryRunVerificationError("dry run timed out before OpenCode completed")

    assistant_text = _latest_assistant_text(client.get_session_messages(session_id))
    if assistant_text != "OK":
        _delete_after_dry_run(client, session_id, delete_session)
        observed = assistant_text if assistant_text is not None else "<missing>"
        raise DryRunVerificationError(f"dry run expected assistant response 'OK', got {observed!r}")

    deleted = _delete_after_dry_run(client, session_id, delete_session)
    status = "dry_run_ok_deleted" if deleted else "dry_run_ok"
    return JobSubmissionResult(
        session_id=session_id,
        title=title,
        status=status,
        deleted=deleted,
        wait_completed=wait_completed,
        dry_run=True,
        verification="assistant_replied_ok",
    )


def _delete_after_dry_run(client: Any, session_id: str, delete_session: bool) -> bool:
    if not delete_session:
        return False
    return bool(client.delete_session(session_id))


def _latest_assistant_text(messages: list[dict[str, Any]]) -> str | None:
    for message in reversed(messages):
        info = message.get("info") if isinstance(message.get("info"), dict) else message
        if info.get("role") != "assistant":
            continue
        text = _message_text(message).strip()
        return text or None
    return None


def _message_text(message: dict[str, Any]) -> str:
    direct = message.get("text") or message.get("content")
    if isinstance(direct, str):
        return direct
    parts = message.get("parts")
    if isinstance(parts, list):
        texts: list[str] = []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
        return "".join(texts)
    return ""


def submit_prompt_with_timeout(
    client: Any,
    *,
    title: str,
    prompt: str,
    model: str,
    provider: str | None,
    agent: str | None,
    wait: bool,
    send_timeout: float,
) -> tuple[str | None, str]:
    session_id = client.create_session(title)
    if not session_id:
        return None, "failed"

    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
    provider_id, model_id = infer_provider(model, provider)

    def send() -> None:
        try:
            result = client.send_message(
                session_id,
                prompt,
                model_id=model_id,
                provider_id=provider_id,
                agent=agent,
            )
            result_queue.put(("result", result))
        except Exception as exc:  # noqa: BLE001 - status is recorded in the batch manifest
            result_queue.put(("error", exc))

    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    thread.join(None if send_timeout <= 0 else send_timeout)

    if thread.is_alive():
        status = "submitted_timeout"
    else:
        kind, result = result_queue.get() if not result_queue.empty() else ("result", None)
        status = "submitted_unconfirmed" if kind == "error" or result is None else "submitted"

    if wait:
        client.wait_for_session_complete(session_id)
        status = "completed"
    return session_id, status
