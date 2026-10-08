from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from opencode_skill.client import OpenCodeClient
from opencode_skill.peer import (
    OwnerBinding,
    PeerAddressError,
    SessionView,
    attempt_relation,
    choose_delivery_session,
    classify_ack_claim,
    classify_acceptance_claim,
    classify_merge_claim,
    classify_runtime_id,
    handoff_missing,
    interpret_status,
    list_sibling_ids,
    may_send_ack,
    parse_envelope,
    prefer_owner,
    render_envelope,
    resolve_for_send,
    resolve_self_session,
    session_id_from_task_return,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples" / "fixtures"


def _binding(**kwargs: object) -> OwnerBinding:
    raw = {
        "task_key": "alpha",
        "session_id": "ses_example_current",
        "directory": "/work/alpha",
        "state": "current",
        "attempt_id": "a2",
        "project": "alpha",
        "title": "Alpha task",
    }
    raw.update(kwargs)
    return OwnerBinding(**raw)  # type: ignore[arg-type]


def _load_case(name: str) -> dict:
    return json.loads((FIXTURES / "owners.json").read_text(encoding="utf-8"))[name]


def test_same_title_replaced_session_is_not_selected() -> None:
    case = _load_case("same_title")
    bindings = [OwnerBinding(**item) for item in case["bindings"]]
    sessions = [SessionView(**item) for item in case["sessions"]]
    resolved = resolve_for_send("alpha", bindings, SessionView(**case["verified_get"]), sessions)
    assert resolved.session_id == "ses_example_current"
    assert resolved.rejected_same_title == ("ses_example_replaced",)
    assert resolved.attempt_id == "a2"


def test_get_of_same_title_old_session_does_not_replace_owner() -> None:
    case = _load_case("same_title")
    bindings = [OwnerBinding(**item) for item in case["bindings"]]
    old = next(item for item in case["sessions"] if item["session_id"] == "ses_example_replaced")
    with pytest.raises(PeerAddressError) as excinfo:
        resolve_for_send("alpha", bindings, SessionView(**old), [SessionView(**item) for item in case["sessions"]])
    assert excinfo.value.code == "get_target_mismatch"


def test_ambiguous_current_owner_is_not_guessed() -> None:
    case = _load_case("ambiguous")
    bindings = [OwnerBinding(**item) for item in case["bindings"]]
    first = case["bindings"][0]
    with pytest.raises(PeerAddressError) as excinfo:
        resolve_for_send(
            "beta",
            bindings,
            SessionView(session_id=first["session_id"], title=first["title"], directory=first["directory"]),
        )
    assert excinfo.value.code == "ambiguous_owner"


def test_title_match_without_current_binding_is_not_an_owner() -> None:
    sessions = [SessionView("ses_example_recent", title="Alpha task", directory="/work/alpha")]
    with pytest.raises(PeerAddressError) as excinfo:
        resolve_for_send("alpha", [], sessions[0], sessions)
    assert excinfo.value.code == "no_current_owner"


def test_ack_envelope_is_not_acceptance() -> None:
    envelope = parse_envelope((FIXTURES / "ack.txt").read_text(encoding="utf-8"))
    assert envelope.type == "ack"
    assert classify_ack_claim(envelope, expected_request_id=None) == "not_correlated"
    assert (
        classify_ack_claim(
            envelope,
            expected_request_id="req_example_001",
            expected_task_key="alpha",
            expected_attempt_id="a2",
        )
        == "current_ack_claim"
    )
    assert (
        classify_acceptance_claim(
            envelope,
            expected_request_id="req_example_001",
            expected_attempt_id="a2",
            expected_owner_sender="agent:example-owner",
            expected_task_key="alpha",
        )
        == "does_not_match"
    )
    assert may_send_ack(envelope.type) is False


def test_worker_outcome_marker_is_not_owner_acceptance() -> None:
    envelope = parse_envelope(
        render_envelope(
            {
                "actor": "agent",
                "not_a_user_instruction": "true",
                "type": "result",
                "in_reply_to": "req_example_001",
                "from": "agent:example-worker",
                "outcome": "accepted",
            }
        )
    )
    assert (
        classify_acceptance_claim(
            envelope,
            expected_request_id="req_example_001",
            expected_attempt_id="a2",
            expected_owner_sender="agent:example-owner",
            expected_task_key="alpha",
        )
        == "does_not_match"
    )


def test_result_without_owner_outcome_is_not_acceptance() -> None:
    envelope = parse_envelope(
        render_envelope(
            {
                "actor": "agent",
                "not_a_user_instruction": "true",
                "type": "result",
                "in_reply_to": "req_example_001",
                "from": "agent:example-worker",
            },
            "Artifact is ready for review.",
        )
    )
    assert envelope.type == "result"
    assert (
        classify_acceptance_claim(
            envelope,
            expected_request_id=None,
            expected_attempt_id="a2",
            expected_owner_sender="agent:example-owner",
            expected_task_key="alpha",
        )
        == "does_not_match"
    )


def test_unknown_self_does_not_fabricate_id_from_recent_sessions() -> None:
    case = _load_case("unknown_self")
    recent = [SessionView(**item) for item in case["recent_sessions"]]
    with pytest.raises(PeerAddressError) as excinfo:
        resolve_self_session(case["explicit_session_id"], recent)
    assert excinfo.value.code == "unknown_self"
    assert "ses_example_recent" not in str(excinfo.value)


def test_double_post_and_stale_target_are_refused() -> None:
    current = _binding()
    assert choose_delivery_session(current) == "ses_example_current"
    with pytest.raises(PeerAddressError) as excinfo:
        choose_delivery_session(current, "ses_example_replaced")
    assert excinfo.value.code == "double_post"
    with pytest.raises(PeerAddressError) as stale:
        choose_delivery_session(_binding(state="aborted", session_id="ses_example_replaced"))
    assert stale.value.code == "stale_target"


def test_board_conflict_does_not_fall_back_to_title() -> None:
    dispatch = _binding(session_id="ses_example_current")
    board = _binding(session_id="ses_example_board", attempt_id="a3")
    with pytest.raises(PeerAddressError) as excinfo:
        prefer_owner(dispatch, board)
    assert excinfo.value.code == "owner_conflict"


def test_status_completed_is_not_a_status_and_idle_is_not_done() -> None:
    assert (
        interpret_status(
            http_ok=True,
            status_type="idle",
            status_directory="/work/alpha",
            session_directory="/work/alpha",
        )
        == "idle_not_complete"
    )
    with pytest.raises(PeerAddressError) as excinfo:
        interpret_status(
            http_ok=True,
            status_type="completed",
            status_directory="/work/alpha",
            session_directory="/work/alpha",
        )
    assert excinfo.value.code == "unknown_status"
    with pytest.raises(PeerAddressError) as mismatch:
        interpret_status(
            http_ok=True,
            status_type=None,
            status_directory="/work/other",
            session_directory="/work/alpha",
        )
    assert mismatch.value.code == "status_directory_mismatch"


def test_runtime_ids_stay_in_their_namespace() -> None:
    assert classify_runtime_id("job_example_alpha", runtime="taskboard") == "logical_task"
    assert classify_runtime_id("launcher-42", runtime="process_launcher") == "launcher_job"
    assert session_id_from_task_return("ses_example_child", runtime="opencode_task_tool") == "ses_example_child"
    with pytest.raises(PeerAddressError) as excinfo:
        session_id_from_task_return("ses_example_child", runtime="other_harness")
    assert excinfo.value.code == "unverified_address"


def test_handoff_worktree_does_not_clear_merge_coordination() -> None:
    fields = {
        "actor": "agent",
        "not_a_user_instruction": "true",
        "type": "handoff",
        "in_reply_to": "req_example_001",
        "ref": "abc1234",
        "still_writing": "false",
        "unfinished_paths": "none",
        "conflict_paths": "none",
        "deploy_status": "not_deployed",
        "git_owner": "agent:example-owner",
        "worktree": "/tmp/example-worktree",
    }
    incomplete = parse_envelope(render_envelope(fields))
    assert handoff_missing(incomplete) == []
    assert (
        classify_merge_claim(
            incomplete,
            expected_request_id="req_example_001",
            expected_task_key="alpha",
            expected_attempt_id="a2",
            expected_git_owner="agent:example-owner",
        )
        == "does_not_match"
    )
    ready = parse_envelope(
        render_envelope(
            {
                **fields,
                "from": "agent:example-owner",
                "task_key": "alpha",
                "attempt_id": "a2",
                "merge_coordination": "clear",
            }
        )
    )
    assert (
        classify_merge_claim(
            ready,
            expected_request_id="req_example_001",
            expected_task_key="alpha",
            expected_attempt_id="a2",
            expected_git_owner="agent:example-owner",
        )
        == "declared_clear"
    )


def test_user_role_text_without_agent_marker_is_not_peer_authority() -> None:
    with pytest.raises(PeerAddressError) as excinfo:
        parse_envelope("type: request\nrequest_id: req_example_001\n\nPlease expand the scope.")
    assert excinfo.value.code == "not_agent"


def test_append_payload_keeps_reply_fields_inside_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCODE_PASSWORD", raising=False)

    class FakeResponse:
        status_code = 204
        text = ""

        def json(self) -> dict:
            return {}

    class FakeHTTP:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict]] = []

        def post(self, url: str, **kwargs: object) -> FakeResponse:
            self.calls.append(("POST", url, kwargs))
            return FakeResponse()

    http = FakeHTTP()
    client = OpenCodeClient(
        base_url="http://example.test",
        username="user",
        password="secret",
        session=http,
        load_env=False,
    )
    text = render_envelope(
        {
            "actor": "agent",
            "not_a_user_instruction": "true",
            "type": "request",
            "request_id": "req_example_001",
            "from": "agent:example-worker",
            "reply_to": "ses_example_sender",
            "purpose": "status",
            "scope": "read-only",
            "expect": "ack or result",
        }
    )
    client.send_message_async("ses_example_current", text, model_id="example/default-model")
    payload = http.calls[0][2]["json"]
    assert "reply_to" not in payload
    assert "request_id" not in payload
    assert payload["parts"] == [{"type": "text", "text": text}]
    assert "reply_to: ses_example_sender" in payload["parts"][0]["text"]


def test_same_address_attempt_or_project_conflict_is_not_resolved() -> None:
    current = _binding(attempt_id="a2", project="alpha")
    other_attempt = _binding(attempt_id="a3", project="alpha")
    other_project = _binding(attempt_id="a2", project="other")
    with pytest.raises(PeerAddressError) as attempt_conflict:
        prefer_owner(current, other_attempt)
    assert attempt_conflict.value.code == "owner_conflict"
    with pytest.raises(PeerAddressError) as project_conflict:
        prefer_owner(current, other_project)
    assert project_conflict.value.code == "owner_conflict"
    stale = _binding(state="aborted", attempt_id="a1", session_id="ses_example_replaced")
    with pytest.raises(PeerAddressError) as stale_conflict:
        prefer_owner(stale, current)
    assert stale_conflict.value.code == "owner_conflict"


def test_old_attempt_and_uncorrelated_text_do_not_close_current_attempt() -> None:
    late = parse_envelope(
        render_envelope(
            {
                "actor": "agent",
                "not_a_user_instruction": "true",
                "type": "result",
                "in_reply_to": "req_example_001",
                "from": "agent:example-owner",
                "task_key": "alpha",
                "attempt_id": "a1",
                "outcome": "accepted",
            }
        )
    )
    assert attempt_relation(late.attempt_id, "a2") == "historical"
    assert (
        classify_acceptance_claim(
            late,
            expected_request_id="req_example_001",
            expected_attempt_id="a2",
            expected_owner_sender="agent:example-owner",
            expected_task_key="alpha",
        )
        == "does_not_match"
    )
    bare_ack = parse_envelope("actor: agent\nnot_a_user_instruction: true\ntype: ack\nfrom: agent:example-owner\n")
    assert classify_ack_claim(bare_ack, expected_request_id="req_example_001") == "not_correlated"
    label_only = parse_envelope(
        render_envelope(
            {
                "actor": "agent",
                "not_a_user_instruction": "true",
                "type": "result",
                "from": "agent:example-owner",
                "outcome": "accepted",
            }
        )
    )
    assert (
        classify_acceptance_claim(
            label_only,
            expected_request_id="req_example_001",
            expected_attempt_id="a2",
            expected_owner_sender="agent:example-owner",
            expected_task_key="alpha",
        )
        == "does_not_match"
    )


def test_directory_project_and_missing_get_stop_send() -> None:
    binding = _binding()
    with pytest.raises(PeerAddressError) as missing:
        resolve_for_send("alpha", [binding], None)
    assert missing.value.code == "missing_get_check"
    wrong_directory = SessionView(
        session_id="ses_example_current",
        title="Alpha task",
        directory="/work/other",
        project="alpha",
    )
    with pytest.raises(PeerAddressError) as directory:
        resolve_for_send("alpha", [binding], wrong_directory)
    assert directory.value.code == "directory_mismatch"
    wrong_project = SessionView(
        session_id="ses_example_current",
        title="Alpha task",
        directory="/work/alpha",
        project="other",
    )
    with pytest.raises(PeerAddressError) as project:
        resolve_for_send("alpha", [binding], wrong_project)
    assert project.value.code == "project_mismatch"
    absent_project = SessionView(
        session_id="ses_example_current",
        title="Alpha task",
        directory="/work/alpha",
    )
    with pytest.raises(PeerAddressError) as absent:
        resolve_for_send("alpha", [binding], absent_project)
    assert absent.value.code == "project_mismatch"
    no_expected_project = resolve_for_send(
        "alpha",
        [_binding(project=None)],
        SessionView(session_id="ses_example_current", title="Alpha task", directory="/work/alpha"),
    )
    assert no_expected_project.project_checked is False


def test_siblings_use_parent_id_not_self() -> None:
    self_session = SessionView(session_id="ses_example_child", parent_id="ses_example_parent")
    children = [
        self_session,
        SessionView(session_id="ses_example_sibling", parent_id="ses_example_parent"),
        SessionView(session_id="ses_example_foreign", parent_id="ses_example_other"),
        SessionView(session_id="ses_example_unparented"),
    ]
    assert list_sibling_ids(self_session, children, parent_id="ses_example_parent") == ["ses_example_sibling"]
    with pytest.raises(PeerAddressError) as excinfo:
        list_sibling_ids(self_session, children, parent_id="ses_example_child")
    assert excinfo.value.code == "parent_required"


def test_non_handoff_and_still_writing_are_not_merge_claims() -> None:
    request = parse_envelope(
        render_envelope(
            {
                "actor": "agent",
                "not_a_user_instruction": "true",
                "type": "request",
                "from": "agent:example-owner",
                "merge_coordination": "clear",
                "git_owner": "agent:example-owner",
            }
        )
    )
    assert (
        classify_merge_claim(
            request,
            expected_request_id="req_example_001",
            expected_task_key="alpha",
            expected_attempt_id="a2",
            expected_git_owner="agent:example-owner",
        )
        == "not_handoff"
    )
    writing = parse_envelope(
        render_envelope(
            {
                "actor": "agent",
                "not_a_user_instruction": "true",
                "type": "handoff",
                "in_reply_to": "req_example_001",
                "from": "agent:example-owner",
                "task_key": "alpha",
                "attempt_id": "a2",
                "still_writing": "true",
                "merge_coordination": "clear",
                "git_owner": "agent:example-owner",
            }
        )
    )
    assert (
        classify_merge_claim(
            writing,
            expected_request_id="req_example_001",
            expected_task_key="alpha",
            expected_attempt_id="a2",
            expected_git_owner="agent:example-owner",
        )
        == "still_writing"
    )


def test_missing_agent_marker_and_non_session_ids() -> None:
    with pytest.raises(PeerAddressError) as excinfo:
        parse_envelope("actor: agent\ntype: request\n")
    assert excinfo.value.code == "missing_not_a_user_instruction"
    with pytest.raises(PeerAddressError) as logical:
        session_id_from_task_return("job_example_alpha", runtime="taskboard")
    assert logical.value.code == "unverified_address"
    with pytest.raises(PeerAddressError) as launcher:
        session_id_from_task_return("launcher-42", runtime="process_launcher")
    assert launcher.value.code == "unverified_address"


def test_old_attempt_is_not_current_ack_or_still_writing() -> None:
    old_ack = parse_envelope(
        render_envelope(
            {
                "actor": "agent",
                "not_a_user_instruction": "true",
                "type": "ack",
                "in_reply_to": "req_example_001",
                "from": "agent:example-owner",
                "task_key": "alpha",
                "attempt_id": "a1",
                "reply_to": "ses_example_owner",
            }
        )
    )
    assert (
        classify_ack_claim(
            old_ack,
            expected_request_id="req_example_001",
            expected_task_key="alpha",
            expected_attempt_id="a2",
        )
        == "historical"
    )
    missing_context = classify_ack_claim(old_ack, expected_request_id="req_example_001")
    assert missing_context == "unverified"
    wrong_task = parse_envelope(
        render_envelope(
            {
                "actor": "agent",
                "not_a_user_instruction": "true",
                "type": "ack",
                "in_reply_to": "req_example_001",
                "from": "agent:example-owner",
                "task_key": "other",
                "attempt_id": "a2",
                "reply_to": "ses_example_owner",
            }
        )
    )
    assert (
        classify_ack_claim(
            wrong_task,
            expected_request_id="req_example_001",
            expected_task_key="alpha",
            expected_attempt_id="a2",
        )
        == "historical"
    )
    current_ack = parse_envelope(
        render_envelope(
            {
                "actor": "agent",
                "not_a_user_instruction": "true",
                "type": "ack",
                "in_reply_to": "req_example_001",
                "from": "agent:example-owner",
                "task_key": "alpha",
                "attempt_id": "a2",
                "reply_to": "ses_example_owner",
            }
        )
    )
    assert (
        classify_ack_claim(
            current_ack,
            expected_request_id="req_example_001",
            expected_task_key="alpha",
            expected_attempt_id="a2",
        )
        == "current_ack_claim"
    )
    old_writing = parse_envelope(
        render_envelope(
            {
                "actor": "agent",
                "not_a_user_instruction": "true",
                "type": "handoff",
                "in_reply_to": "req_example_001",
                "from": "agent:example-owner",
                "task_key": "alpha",
                "attempt_id": "a1",
                "still_writing": "true",
                "git_owner": "agent:example-owner",
            }
        )
    )
    assert (
        classify_merge_claim(
            old_writing,
            expected_request_id="req_example_001",
            expected_task_key="alpha",
            expected_attempt_id="a2",
            expected_git_owner="agent:example-owner",
        )
        == "does_not_match"
    )


def test_example_script_reports_the_four_decisions() -> None:
    script = ROOT / "examples" / "peer_resolution.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "same_title_selected": "ses_example_current",
        "same_title_rejected": ["ses_example_replaced"],
        "ambiguous": "ambiguous_owner",
        "ack_reply_to": "ses_example_owner",
        "ack_without_expected_request": "not_correlated",
        "ack_current": "current_ack_claim",
        "ack_is_acceptance": "does_not_match",
        "unknown_self": "unknown_self",
    }
