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
    wait: bool = True,
    delete_session: bool = False,
    send_timeout: float | None = None,
    wait_poll_interval: float = 15.0,
    wait_max_seconds: float = 7200.0,
) -> JobSubmissionResult:
    provider_id, model_id = infer_provider(model, provider)
    session_id = client.create_session(title)
    client.send_message(
        session_id,
        prompt,
        model_id=model_id,
        provider_id=provider_id,
        agent=agent,
        timeout=send_timeout,
    )
    wait_completed = None
    status = "submitted"
    if wait:
        wait_completed = client.wait_for_session_complete(
            session_id,
            poll_interval=wait_poll_interval,
            max_wait=wait_max_seconds,
        )
        status = "completed" if wait_completed else "wait_timeout"
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
