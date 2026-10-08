from __future__ import annotations

import re
from pathlib import Path

from opencode_skill.peer import classify_acceptance_claim, parse_envelope


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "skill_opencode_agent_to_agent.md"
TEMPLATES = ROOT / "skills" / "templates"

SESSION_ID = re.compile(r"ses_(?!example(?:_|$)|test(?:_|$))[A-Za-z0-9]+")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@")


def test_skill_routes_and_template_links_resolve() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    submission = (ROOT / "skills" / "skill_opencode_submission.md").read_text(encoding="utf-8")
    assert "skills/skill_opencode_agent_to_agent.md" in readme
    assert "skills/skill_opencode_agent_to_agent.md" in agents
    assert "skill_opencode_agent_to_agent.md" in submission
    for name in (
        "agent_to_agent_request.md",
        "agent_to_agent_ack.md",
        "agent_to_agent_result.md",
        "agent_to_agent_handoff.md",
    ):
        assert f"templates/{name}" in skill
        assert (TEMPLATES / name).is_file()
    assert (ROOT / "examples" / "peer_resolution.py").is_file()


def test_templates_parse_and_ack_is_not_acceptance() -> None:
    ack = parse_envelope((TEMPLATES / "agent_to_agent_ack.md").read_text(encoding="utf-8"))
    result = parse_envelope((TEMPLATES / "agent_to_agent_result.md").read_text(encoding="utf-8"))
    handoff = parse_envelope((TEMPLATES / "agent_to_agent_handoff.md").read_text(encoding="utf-8"))
    request = parse_envelope((TEMPLATES / "agent_to_agent_request.md").read_text(encoding="utf-8"))
    assert request.type == "request"
    assert ack.type == "ack"
    assert ack.reply_to == "ses_example_owner"
    assert ack.reply_to != "ses_example_sender"
    assert (
        classify_acceptance_claim(
            ack,
            expected_request_id="req_example_001",
            expected_attempt_id="a2",
            expected_owner_sender="agent:example-owner",
            expected_task_key="alpha",
        )
        == "does_not_match"
    )
    assert result.type == "result"
    assert (
        classify_acceptance_claim(
            result,
            expected_request_id="req_example_001",
            expected_attempt_id="a2",
            expected_owner_sender="agent:example-owner",
            expected_task_key="alpha",
        )
        == "does_not_match"
    )
    assert handoff.type == "handoff"
    assert handoff.fields["merge_coordination"] == "required"
    assert "worktree" in handoff.fields


def test_new_public_files_have_no_private_markers() -> None:
    paths = [
        SKILL,
        ROOT / "examples" / "peer_resolution.py",
        ROOT / "examples" / "fixtures" / "owners.json",
        ROOT / "examples" / "fixtures" / "ack.txt",
        ROOT / "src" / "opencode_skill" / "peer.py",
        *TEMPLATES.glob("agent_to_agent_*.md"),
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "/Users/" not in text
        assert SESSION_ID.search(text) is None, path.name
        assert EMAIL.search(text) is None, path.name
        assert "4096" not in text
