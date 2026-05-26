from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from opencode_skill import batch


def parse(argv: list[str]):
    parser = batch.build_parser()
    args = parser.parse_args(argv)
    batch.validate_args(args)
    return args


def write_specs(root: Path, *slugs: str) -> Path:
    specs = root / "specs"
    specs.mkdir()
    for slug in slugs:
        (specs / f"{slug}.md").write_text(f"# {slug}\\n", encoding="utf-8")
    return specs


def test_submit_dry_run_renders_prompts_and_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    specs = write_specs(tmp_path, "b_slug", "a_slug")
    template = tmp_path / "template.md"
    template.write_text("slug={{SLUG}} spec={{SPEC_PATH}} custom={{CITY}}", encoding="utf-8")
    out = tmp_path / "out"

    args = parse(["submit", "--template", str(template), "--specs", str(specs), "--output-root", str(out), "--var", "CITY=London", "--dry-run", "--batch-id", "batch123"])

    assert batch.run(args) == 0
    payload = json.loads(capsys.readouterr().out)
    manifest = json.loads(Path(payload["manifest"]).read_text(encoding="utf-8"))

    assert manifest["operation"] == "submit"
    assert manifest["summary"] == {"total": 2, "submitted": 0, "failed": 0, "verify_passed": 0}
    assert [item["slug"] for item in manifest["slugs"]] == ["a_slug", "b_slug"]
    rendered = out / "batch123" / "templates" / "rendered_a_slug.md"
    assert "slug=a_slug" in rendered.read_text(encoding="utf-8")
    assert "custom=London" in rendered.read_text(encoding="utf-8")


def test_submit_dry_run_supports_shell_style_alias_variables(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    specs = write_specs(tmp_path, "alpha")
    template = tmp_path / "template.md"
    template.write_text("slug=${TARGET_SLUG} spec=${DESTINATION_SPEC_FILE} wave=${WAVE_DIR} batch={{BATCH_ID}}", encoding="utf-8")
    out = tmp_path / "out"

    assert batch.run(parse(["submit", "--template", str(template), "--specs", str(specs), "--output-root", str(out), "--dry-run", "--batch-id", "batch_alias"])) == 0
    capsys.readouterr()
    rendered = (out / "batch_alias" / "templates" / "rendered_alpha.md").read_text(encoding="utf-8")
    assert "slug=alpha" in rendered
    assert f"spec={specs / 'alpha.md'}" in rendered
    assert f"wave={out / 'batch_alias'}" in rendered
    assert "${" not in rendered
    assert "{{" not in rendered


def test_submit_dry_run_fails_on_unresolved_template_variable(tmp_path: Path) -> None:
    specs = write_specs(tmp_path, "alpha")
    template = tmp_path / "template.md"
    template.write_text("slug={{SLUG}} missing=${MISSING_VAR}", encoding="utf-8")
    args = parse(["submit", "--template", str(template), "--specs", str(specs), "--output-root", str(tmp_path / "out"), "--dry-run"])

    with pytest.raises(ValueError, match=r"Unresolved template variables: \$\{MISSING_VAR\}"):
        batch.run(args)


def test_submit_dry_run_fails_on_bare_template_token(tmp_path: Path) -> None:
    specs = write_specs(tmp_path, "alpha")
    template = tmp_path / "template.md"
    template.write_text("read NODE_SPEC_FILE for slug {{SLUG}}", encoding="utf-8")
    args = parse(["submit", "--template", str(template), "--specs", str(specs), "--output-root", str(tmp_path / "out"), "--dry-run"])

    with pytest.raises(ValueError, match="Bare template variable tokens remain"):
        batch.run(args)


def test_submit_filters_specs_by_smoke_and_slugs(tmp_path: Path) -> None:
    specs = write_specs(tmp_path, "one", "two", "three")
    template = tmp_path / "template.md"
    template.write_text("slug={{SLUG}}", encoding="utf-8")
    args = parse(["submit", "--template", str(template), "--specs", str(specs), "--output-root", str(tmp_path / "out"), "--slugs", "three,one", "--smoke-slug", "three", "--dry-run", "--batch-id", "subset01"])

    assert batch.run(args) == 0
    manifest = json.loads((tmp_path / "out" / "subset01" / "batch_manifest.json").read_text(encoding="utf-8"))
    assert [item["slug"] for item in manifest["slugs"]] == ["three"]
    assert not (tmp_path / "out" / "subset01" / "templates" / "rendered_one.md").exists()


def test_template_dir_selects_model_mode_template(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "single_agent.md").write_text("single {{SLUG}}", encoding="utf-8")
    specs = write_specs(tmp_path, "alpha")
    args = parse(["submit", "--template-dir", str(templates), "--specs", str(specs), "--output-root", str(tmp_path / "out"), "--model-mode", "single_agent", "--dry-run", "--batch-id", "tmpl001"])

    assert batch.run(args) == 0
    rendered = tmp_path / "out" / "tmpl001" / "templates" / "rendered_alpha.md"
    assert rendered.read_text(encoding="utf-8") == "single alpha"


def test_qa_dry_run_groups_slugs_and_manifest(tmp_path: Path) -> None:
    args = parse(["qa", "--slugs", "s1,s2,s3", "--output-root", str(tmp_path / "out"), "--group-size", "2", "--dry-run", "--batch-id", "qa001"])

    assert batch.run(args) == 0
    manifest = json.loads((tmp_path / "out" / "qa001" / "batch_manifest.json").read_text(encoding="utf-8"))
    assert manifest["operation"] == "qa"
    assert len(manifest["groups"]) == 2
    assert manifest["groups"][0]["slugs"] == ["s1", "s2"]
    assert manifest["groups"][1]["slugs"] == ["s3"]


def test_qa_from_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source_manifest.json"
    source.write_text(json.dumps({"operation": "submit", "slugs": [{"slug": "a"}, {"slug": "b"}]}), encoding="utf-8")
    args = parse(["qa", "--slugs-from-manifest", str(source), "--output-root", str(tmp_path / "out"), "--dry-run", "--batch-id", "qaman"])

    assert batch.run(args) == 0
    manifest = json.loads((tmp_path / "out" / "qaman" / "batch_manifest.json").read_text(encoding="utf-8"))
    assert manifest["groups"][0]["slugs"] == ["a", "b"]


def test_batch_title_pattern_must_start_with_batch(tmp_path: Path) -> None:
    specs = write_specs(tmp_path, "alpha")
    template = tmp_path / "template.md"
    template.write_text("slug={{SLUG}}", encoding="utf-8")
    args = parse(["submit", "--template", str(template), "--specs", str(specs), "--output-root", str(tmp_path / "out"), "--title-pattern", "job-{slug}", "--dry-run"])

    with pytest.raises(SystemExit):
        batch.run(args)


def test_rate_limit_uses_injected_sleep_for_live_submit(tmp_path: Path) -> None:
    specs = write_specs(tmp_path, "a", "b", "c")
    template = tmp_path / "template.md"
    template.write_text("slug={{SLUG}}", encoding="utf-8")
    sleeps: list[float] = []

    class FakeClient:
        def __init__(self) -> None:
            self.count = 0

        def create_session(self, _title: str) -> str:
            self.count += 1
            return f"ses_{self.count}"

        def send_message(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
            return {"ok": "yes"}

    args = parse(["submit", "--template", str(template), "--specs", str(specs), "--output-root", str(tmp_path / "out"), "--rate-limit", "0.5", "--batch-id", "rate001"])

    assert batch.run(args, client_factory=FakeClient, sleep_fn=sleeps.append) == 0
    assert sleeps == [0.5, 0.5]
    manifest = json.loads((tmp_path / "out" / "rate001" / "batch_manifest.json").read_text(encoding="utf-8"))
    assert [item["session_id"] for item in manifest["slugs"]] == ["ses_1", "ses_2", "ses_3"]


def test_live_submit_times_out_send_message_and_continues(tmp_path: Path) -> None:
    specs = write_specs(tmp_path, "a", "b")
    template = tmp_path / "template.md"
    template.write_text("slug={{SLUG}}", encoding="utf-8")
    send_started: list[str] = []
    release = threading.Event()

    class SlowClient:
        def __init__(self) -> None:
            self.count = 0

        def create_session(self, _title: str) -> str:
            self.count += 1
            return f"ses_{self.count}"

        def send_message(self, *args: Any, **_kwargs: Any) -> dict[str, str]:
            send_started.append(args[0])
            release.wait(1)
            return {"ok": "late"}

    args = parse(["submit", "--template", str(template), "--specs", str(specs), "--output-root", str(tmp_path / "out"), "--send-timeout", "0.01", "--rate-limit", "0", "--batch-id", "timeout001"])

    try:
        assert batch.run(args, client_factory=SlowClient) == 0
    finally:
        release.set()
    manifest = json.loads((tmp_path / "out" / "timeout001" / "batch_manifest.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in manifest["slugs"]] == ["submitted_timeout", "submitted_timeout"]
    assert send_started == ["ses_1", "ses_2"]


def test_parser_requires_operation(tmp_path: Path) -> None:
    parser = batch.build_parser()
    with pytest.raises(SystemExit):
        args = parser.parse_args(["--output-root", str(tmp_path)])
        batch.validate_args(args)
