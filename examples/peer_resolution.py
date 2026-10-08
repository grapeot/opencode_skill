from __future__ import annotations

import json
import sys
from pathlib import Path

from opencode_skill.peer import (
    OwnerBinding,
    PeerAddressError,
    SessionView,
    classify_acceptance_claim,
    classify_ack_claim,
    parse_envelope,
    resolve_for_send,
    resolve_self_session,
)


def _binding(raw: dict) -> OwnerBinding:
    return OwnerBinding(
        task_key=raw["task_key"],
        session_id=raw["session_id"],
        directory=raw["directory"],
        state=raw["state"],
        attempt_id=raw.get("attempt_id"),
        project=raw.get("project"),
        title=raw.get("title"),
    )


def _session(raw: dict) -> SessionView:
    return SessionView(
        session_id=raw["session_id"],
        title=raw.get("title"),
        directory=raw.get("directory"),
        project=raw.get("project"),
        parent_id=raw.get("parent_id"),
    )


def run(fixture_dir: Path | None = None) -> dict:
    root = fixture_dir or Path(__file__).resolve().parent / "fixtures"
    payload = json.loads((root / "owners.json").read_text(encoding="utf-8"))
    same = payload["same_title"]
    resolved = resolve_for_send(
        "alpha",
        [_binding(item) for item in same["bindings"]],
        _session(same["verified_get"]),
        [_session(item) for item in same["sessions"]],
    )
    ambiguous_code = "selected"
    try:
        first = payload["ambiguous"]["bindings"][0]
        resolve_for_send(
            "beta",
            [_binding(item) for item in payload["ambiguous"]["bindings"]],
            _session(first),
        )
    except PeerAddressError as exc:
        ambiguous_code = exc.code
    unknown = payload["unknown_self"]
    unknown_code = "selected"
    try:
        resolve_self_session(
            unknown["explicit_session_id"],
            [_session(item) for item in unknown["recent_sessions"]],
        )
    except PeerAddressError as exc:
        unknown_code = exc.code
    ack = parse_envelope((root / "ack.txt").read_text(encoding="utf-8"))
    return {
        "same_title_selected": resolved.session_id,
        "same_title_rejected": list(resolved.rejected_same_title),
        "ambiguous": ambiguous_code,
        "ack_reply_to": ack.reply_to,
        "ack_without_expected_request": classify_ack_claim(ack, expected_request_id=None),
        "ack_current": classify_ack_claim(
            ack,
            expected_request_id="req_example_001",
            expected_task_key="alpha",
            expected_attempt_id="a2",
        ),
        "ack_is_acceptance": classify_acceptance_claim(
            ack,
            expected_request_id="req_example_001",
            expected_attempt_id="a2",
            expected_owner_sender="agent:example-owner",
            expected_task_key="alpha",
        ),
        "unknown_self": unknown_code,
    }


def main() -> int:
    json.dump(run(), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
