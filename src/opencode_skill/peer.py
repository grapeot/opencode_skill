from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence


STATUS_TYPES = frozenset({"idle", "retry", "busy"})
MESSAGE_TYPES = frozenset({"request", "ack", "result", "handoff"})
REQUEST_FIELDS = ("request_id", "from", "reply_to", "purpose", "scope", "expect")
HANDOFF_FIELDS = (
    "ref",
    "still_writing",
    "unfinished_paths",
    "conflict_paths",
    "deploy_status",
    "git_owner",
)
HEADER_ORDER = (
    "actor",
    "not_a_user_instruction",
    "type",
    "request_id",
    "in_reply_to",
    "from",
    "task_key",
    "attempt_id",
    "reply_to",
    "purpose",
    "scope",
    "artifacts",
    "expect",
    "outcome",
    "ref",
    "still_writing",
    "unfinished_paths",
    "conflict_paths",
    "deploy_status",
    "git_owner",
    "merge_coordination",
    "worktree",
)


class PeerAddressError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OwnerBinding:
    task_key: str
    session_id: str
    directory: str
    state: str
    attempt_id: str | None = None
    project: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class SessionView:
    session_id: str
    title: str | None = None
    directory: str | None = None
    project: str | None = None
    parent_id: str | None = None


@dataclass(frozen=True)
class ResolvedTarget:
    task_key: str
    session_id: str
    directory: str
    attempt_id: str | None = None
    project: str | None = None
    project_checked: bool = False
    rejected_same_title: tuple[str, ...] = ()


@dataclass(frozen=True)
class PeerEnvelope:
    type: str
    fields: Mapping[str, str]
    body: str = ""
    sender: str | None = None
    request_id: str | None = None
    in_reply_to: str | None = None
    task_key: str | None = None
    attempt_id: str | None = None
    reply_to: str | None = None
    extra: Mapping[str, str] = field(default_factory=dict)


def classify_runtime_id(value: str, *, runtime: str) -> str:
    text = value.strip()
    if not text:
        raise PeerAddressError("empty_address")
    if text.startswith("job_"):
        return "logical_task"
    if runtime == "process_launcher":
        return "launcher_job"
    if runtime == "opencode_task_tool" and text.startswith("ses_"):
        return "session"
    return "unverified"


def session_id_from_task_return(value: str, *, runtime: str) -> str:
    kind = classify_runtime_id(value, runtime=runtime)
    if kind != "session":
        raise PeerAddressError("unverified_address")
    return value.strip()


def resolve_self_session(
    explicit_session_id: str | None,
    recent_sessions: Sequence[SessionView] | None = None,
) -> str:
    del recent_sessions
    if explicit_session_id is None or not str(explicit_session_id).strip():
        raise PeerAddressError("unknown_self")
    return str(explicit_session_id).strip()


def list_sibling_ids(
    self_session: SessionView | None,
    children: Sequence[SessionView],
    *,
    parent_id: str,
) -> list[str]:
    if self_session is None or not self_session.session_id:
        raise PeerAddressError("unknown_self")
    if not parent_id or parent_id == self_session.session_id or self_session.parent_id != parent_id:
        raise PeerAddressError("parent_required")
    return [
        child.session_id
        for child in children
        if child.session_id != self_session.session_id and child.parent_id == self_session.parent_id
    ]


def resolve_current_owner(
    task_key: str,
    bindings: Sequence[OwnerBinding],
    sessions: Sequence[SessionView] | None = None,
    *,
    verified_get: SessionView | None = None,
) -> ResolvedTarget:
    if not task_key.strip():
        raise PeerAddressError("no_current_owner")
    matched = [item for item in bindings if item.task_key == task_key]
    current = [item for item in matched if item.state == "current"]
    if not current:
        raise PeerAddressError("no_current_owner")
    identities = {(item.session_id, item.attempt_id, item.directory, item.project) for item in current}
    if len(identities) != 1:
        raise PeerAddressError("ambiguous_owner")
    binding = current[0]
    if not binding.session_id or not binding.directory:
        raise PeerAddressError("no_current_owner")
    if verified_get is not None:
        _check_verified_get(binding, verified_get)
    rejected = _same_title_others(binding, verified_get, sessions or ())
    return ResolvedTarget(
        task_key=binding.task_key,
        session_id=binding.session_id,
        directory=binding.directory,
        attempt_id=binding.attempt_id,
        project=binding.project if binding.project and verified_get and verified_get.project == binding.project else None,
        project_checked=bool(binding.project and verified_get and verified_get.project == binding.project),
        rejected_same_title=rejected,
    )


def resolve_for_send(
    task_key: str,
    bindings: Sequence[OwnerBinding],
    verified_get: SessionView | None,
    sessions: Sequence[SessionView] | None = None,
) -> ResolvedTarget:
    if verified_get is None:
        raise PeerAddressError("missing_get_check")
    return resolve_current_owner(task_key, bindings, sessions, verified_get=verified_get)


def prefer_owner(primary: OwnerBinding | None, board: OwnerBinding | None) -> OwnerBinding:
    if primary is None and board is None:
        raise PeerAddressError("no_current_owner")
    if board is None:
        if primary is None or primary.state != "current" or not primary.attempt_id:
            raise PeerAddressError("stale_target")
        return primary
    if primary is None:
        if board.state != "current" or not board.attempt_id:
            raise PeerAddressError("stale_target")
        return board
    if not _owners_agree(primary, board):
        raise PeerAddressError("owner_conflict")
    return primary


def choose_delivery_session(owner: OwnerBinding, *extra: str) -> str:
    if owner.state != "current":
        raise PeerAddressError("stale_target")
    for item in extra:
        if item and item != owner.session_id:
            raise PeerAddressError("double_post")
    return owner.session_id


def interpret_status(
    *,
    http_ok: bool,
    status_type: str | None,
    status_directory: str,
    session_directory: str,
) -> str:
    if status_directory != session_directory:
        raise PeerAddressError("status_directory_mismatch")
    if not http_ok:
        return "not_reachable"
    if status_type is None:
        return "absent_from_nonidle_map"
    if status_type not in STATUS_TYPES:
        raise PeerAddressError("unknown_status")
    if status_type == "idle":
        return "idle_not_complete"
    if status_type == "busy":
        return "busy_not_accepted"
    return status_type


def render_envelope(fields: Mapping[str, str], body: str = "") -> str:
    ordered = [key for key in HEADER_ORDER if key in fields]
    ordered.extend(key for key in fields if key not in ordered)
    lines = [f"{key}: {fields[key]}" for key in ordered]
    text = "\n".join(lines)
    if body.strip():
        return text + "\n\n" + body.strip() + "\n"
    return text + "\n"


def parse_envelope(text: str) -> PeerEnvelope:
    header, body = _split_header(text)
    fields: dict[str, str] = {}
    for line in header.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise PeerAddressError("malformed_envelope")
        key, value = stripped.split(":", 1)
        fields[key.strip()] = value.strip()
    if fields.get("actor") != "agent":
        raise PeerAddressError("not_agent")
    if fields.get("not_a_user_instruction") != "true":
        raise PeerAddressError("missing_not_a_user_instruction")
    message_type = fields.get("type", "")
    if message_type not in MESSAGE_TYPES:
        raise PeerAddressError("malformed_envelope")
    known = set(HEADER_ORDER)
    extra = {key: value for key, value in fields.items() if key not in known}
    return PeerEnvelope(
        type=message_type,
        fields=fields,
        body=body.strip(),
        sender=fields.get("from") or None,
        request_id=fields.get("request_id") or None,
        in_reply_to=fields.get("in_reply_to") or None,
        task_key=fields.get("task_key") or None,
        attempt_id=fields.get("attempt_id") or None,
        reply_to=fields.get("reply_to") or None,
        extra=extra,
    )


def missing_request_fields(envelope: PeerEnvelope, *, has_task: bool, has_artifacts: bool) -> list[str]:
    missing = [key for key in REQUEST_FIELDS if not envelope.fields.get(key)]
    if has_task and not envelope.fields.get("task_key"):
        missing.append("task_key")
    if has_task and not envelope.fields.get("attempt_id"):
        missing.append("attempt_id")
    if has_artifacts and not envelope.fields.get("artifacts"):
        missing.append("artifacts")
    return missing


def declared_message_type(envelope: PeerEnvelope) -> str:
    return envelope.type


def message_scope(
    envelope: PeerEnvelope,
    *,
    expected_request_id: str | None,
    expected_task_key: str | None,
    expected_attempt_id: str | None,
) -> str:
    if not expected_request_id or envelope.in_reply_to != expected_request_id:
        return "not_correlated"
    if not expected_task_key or not expected_attempt_id or not envelope.task_key or not envelope.attempt_id:
        return "unverified"
    if envelope.task_key != expected_task_key or envelope.attempt_id != expected_attempt_id:
        return "historical"
    return "current"


def classify_ack_claim(
    envelope: PeerEnvelope,
    *,
    expected_request_id: str | None,
    expected_task_key: str | None = None,
    expected_attempt_id: str | None = None,
) -> str:
    if envelope.type != "ack":
        return "not_correlated"
    scope = message_scope(
        envelope,
        expected_request_id=expected_request_id,
        expected_task_key=expected_task_key,
        expected_attempt_id=expected_attempt_id,
    )
    if scope == "current":
        return "current_ack_claim"
    if scope == "historical":
        return "historical"
    if scope == "not_correlated":
        return "not_correlated"
    return "unverified"


def classify_acceptance_claim(
    envelope: PeerEnvelope,
    *,
    expected_request_id: str | None,
    expected_attempt_id: str | None,
    expected_owner_sender: str | None,
    expected_task_key: str | None,
) -> str:
    if envelope.type != "result" or envelope.fields.get("outcome") != "accepted" or not expected_owner_sender:
        return "does_not_match"
    if (
        message_scope(
            envelope,
            expected_request_id=expected_request_id,
            expected_task_key=expected_task_key,
            expected_attempt_id=expected_attempt_id,
        )
        != "current"
    ):
        return "does_not_match"
    if envelope.sender != expected_owner_sender:
        return "does_not_match"
    return "matches_declared_context"


def attempt_relation(envelope_attempt_id: str | None, current_attempt_id: str | None) -> str:
    if not current_attempt_id or not envelope_attempt_id:
        return "unverified"
    if envelope_attempt_id != current_attempt_id:
        return "historical"
    return "current"


def may_send_ack(inbound_type: str) -> bool:
    return inbound_type != "ack"


def handoff_missing(envelope: PeerEnvelope) -> list[str]:
    if envelope.type != "handoff":
        return []
    missing = [key for key in HANDOFF_FIELDS if not envelope.fields.get(key)]
    if envelope.fields.get("still_writing") not in {"true", "false"}:
        if "still_writing" not in missing:
            missing.append("still_writing")
    return missing


def classify_merge_claim(
    envelope: PeerEnvelope,
    *,
    expected_request_id: str | None,
    expected_task_key: str | None,
    expected_attempt_id: str | None,
    expected_git_owner: str | None,
) -> str:
    if envelope.type != "handoff":
        return "not_handoff"
    scope = message_scope(
        envelope,
        expected_request_id=expected_request_id,
        expected_task_key=expected_task_key,
        expected_attempt_id=expected_attempt_id,
    )
    if scope != "current":
        return "does_not_match"
    if envelope.fields.get("still_writing") == "true":
        return "still_writing"
    if (
        not expected_git_owner
        or envelope.sender != expected_git_owner
        or envelope.fields.get("git_owner") != expected_git_owner
        or envelope.fields.get("merge_coordination") != "clear"
        or envelope.fields.get("still_writing") != "false"
    ):
        return "does_not_match"
    return "declared_clear"


def _check_verified_get(binding: OwnerBinding, verified_get: SessionView) -> None:
    if verified_get.session_id != binding.session_id:
        raise PeerAddressError("get_target_mismatch")
    if not verified_get.directory:
        raise PeerAddressError("directory_mismatch")
    if verified_get.directory != binding.directory:
        raise PeerAddressError("directory_mismatch")
    if binding.project and verified_get.project != binding.project:
        raise PeerAddressError("project_mismatch")


def _owners_agree(primary: OwnerBinding, board: OwnerBinding) -> bool:
    return (
        primary.task_key == board.task_key
        and primary.state == "current"
        and board.state == "current"
        and primary.session_id == board.session_id
        and primary.directory == board.directory
        and bool(primary.attempt_id)
        and primary.attempt_id == board.attempt_id
        and primary.project == board.project
    )


def _same_title_others(
    binding: OwnerBinding,
    verified_get: SessionView | None,
    sessions: Sequence[SessionView],
) -> tuple[str, ...]:
    title = (verified_get.title if verified_get is not None else None) or binding.title
    if not title:
        return ()
    return tuple(
        session.session_id
        for session in sessions
        if session.session_id != binding.session_id and session.title == title
    )


def _split_header(text: str) -> tuple[str, str]:
    if "\n\n" in text:
        header, body = text.split("\n\n", 1)
        return header, body
    return text, ""
