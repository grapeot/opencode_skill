from __future__ import annotations

import argparse
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .client import OpenCodeClient
from .jobs import submit_prompt_with_timeout

DEFAULT_RATE_LIMIT_SECONDS = 1.0
DEFAULT_SEND_TIMEOUT_SECONDS = 10.0
DEFAULT_GROUP_SIZE = 10
DEFAULT_MODEL_MODE = "custom"
DEFAULT_QA_TEMPLATE = """QA mode: {{QA_MODE}}
Group {{GROUP_INDEX}} of {{GROUP_COUNT}}
Slugs: {{GROUP_SLUGS}}
Report path: {{QA_REPORT_PATH}}

Review the listed slugs according to the user's project instructions. In audit_only mode, report findings only. In fix_small_issues mode, make only small local fixes and summarize them.
"""


@dataclass(frozen=True)
class SpecItem:
    slug: str
    path: Path


@dataclass(frozen=True)
class BatchPaths:
    root: Path
    manifest: Path
    submit_log: Path
    slug_logs: Path
    templates: Path
    groups: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_model() -> str:
    return os.environ.get("OPENCODE_BATCH_MODEL") or os.environ.get("OPENCODE_MODEL") or "example/default-model"


def default_agent() -> str | None:
    return os.environ.get("OPENCODE_BATCH_AGENT") or os.environ.get("OPENCODE_AGENT")


def parse_vars(values: Iterable[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in values or []:
        if "=" not in item:
            raise ValueError(f"--var must use KEY=VALUE format: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("--var key cannot be empty")
        result[key] = value
    return result


def split_slugs(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def slug_from_spec(path: Path) -> str:
    return path.stem


def discover_specs(specs_path: str | Path | None) -> list[SpecItem]:
    if not specs_path:
        return []
    path = Path(specs_path).expanduser().resolve()
    if path.is_file():
        return [SpecItem(slug_from_spec(path), path)]
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".md")
        return [SpecItem(slug_from_spec(p), p) for p in files]
    raise FileNotFoundError(f"Spec path not found: {path}")


def slugs_from_manifest(path: str | Path | None) -> list[str]:
    if not path:
        return []
    manifest = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if manifest.get("operation") == "qa":
        slugs: list[str] = []
        for group in manifest.get("groups", []):
            slugs.extend(group.get("slugs", []))
        return slugs
    return [item["slug"] for item in manifest.get("slugs", []) if "slug" in item]


def group_slugs(slugs: list[str], group_size: int) -> list[list[str]]:
    if group_size < 1:
        raise ValueError("group_size must be >= 1")
    return [slugs[index:index + group_size] for index in range(0, len(slugs), group_size)]


UNRESOLVED_TEMPLATE_PATTERN = re.compile(r"({{[A-Za-z_][A-Za-z0-9_]*}}|\$\{[A-Za-z_][A-Za-z0-9_]*\})")


def find_unresolved_template_variables(rendered: str) -> list[str]:
    return sorted(set(UNRESOLVED_TEMPLATE_PATTERN.findall(rendered)))


def find_bare_template_tokens(rendered: str, variables: dict[str, Any]) -> list[str]:
    tokens = []
    for key in variables:
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", rendered):
            tokens.append(key)
    return sorted(set(tokens))


def render_template(template: str, variables: dict[str, Any]) -> str:
    rendered = template
    for key, value in variables.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))
        rendered = rendered.replace("${" + key + "}", str(value))
    unresolved = find_unresolved_template_variables(rendered)
    if unresolved:
        raise ValueError("Unresolved template variables: " + ", ".join(unresolved))
    bare_tokens = find_bare_template_tokens(rendered, variables)
    if bare_tokens:
        raise ValueError("Bare template variable tokens remain after rendering: " + ", ".join(bare_tokens))
    return rendered


def validate_template_for_mode(_template_text: str, model_mode: str) -> None:
    if model_mode not in {"custom", "single_agent", "controller"}:
        raise ValueError(f"unknown model mode: {model_mode}")


def resolve_template(args: argparse.Namespace) -> tuple[str, Path | None]:
    if args.operation == "qa":
        if args.qa_template:
            path = Path(args.qa_template).expanduser().resolve()
            return path.read_text(encoding="utf-8"), path
        if args.template_dir:
            path = Path(args.template_dir).expanduser().resolve() / "qa.md"
            if not path.exists():
                raise FileNotFoundError(f"QA template not found: {path}")
            return path.read_text(encoding="utf-8"), path
        return DEFAULT_QA_TEMPLATE, None

    if args.template:
        path = Path(args.template).expanduser().resolve()
        text = path.read_text(encoding="utf-8")
        validate_template_for_mode(text, args.model_mode)
        return text, path
    if args.template_dir:
        path = Path(args.template_dir).expanduser().resolve() / f"{args.model_mode}.md"
        if not path.exists():
            raise FileNotFoundError(f"Template not found for model mode {args.model_mode}: {path}")
        text = path.read_text(encoding="utf-8")
        validate_template_for_mode(text, args.model_mode)
        return text, path
    raise ValueError("submit operation requires --template or --template-dir")


def make_batch_paths(output_root: str | Path, batch_id: str) -> BatchPaths:
    root = Path(output_root).expanduser().resolve() / batch_id
    return BatchPaths(
        root=root,
        manifest=root / "batch_manifest.json",
        submit_log=root / "submit.log",
        slug_logs=root / "slugs",
        templates=root / "templates",
        groups=root / "groups",
    )


def ensure_batch_dirs(paths: BatchPaths) -> None:
    paths.slug_logs.mkdir(parents=True, exist_ok=True)
    paths.templates.mkdir(parents=True, exist_ok=True)
    paths.groups.mkdir(parents=True, exist_ok=True)


def title_for_submit(pattern: str, batch_id: str, slug: str, timestamp: str) -> str:
    return pattern.format(batch_id=batch_id, slug=slug, timestamp=timestamp)


def title_for_group(pattern: str, batch_id: str, group_index: int, group_count: int) -> str:
    return pattern.format(batch_id=batch_id, group_index=group_index, group_count=group_count)


def verify_session(client: Any, session_id: str | None) -> dict[str, bool]:
    if not session_id:
        return {"session_exists": False, "has_assistant_message": False}
    info = client.get_session_info(session_id)
    messages = client.get_session_messages(session_id) or []
    return {
        "session_exists": bool(info),
        "has_assistant_message": any((message.get("info") or message).get("role") == "assistant" for message in messages),
    }


def build_submit_manifest(
    args: argparse.Namespace,
    batch_id: str,
    template_path: Path | None,
    specs: list[SpecItem],
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    failed = sum(1 for item in entries if item.get("status") == "failed")
    submitted = sum(1 for item in entries if item.get("status") in {"submitted", "submitted_unconfirmed", "submitted_timeout", "completed"})
    verify_passed = sum(1 for item in entries if item.get("verify", {}).get("has_assistant_message"))
    return {
        "batch_id": batch_id,
        "operation": "submit",
        "model_mode": args.model_mode,
        "created_at": utc_now(),
        "model": args.model,
        "agent": args.agent,
        "template_file": str(template_path) if template_path else None,
        "rate_limit_seconds": args.rate_limit,
        "send_timeout_seconds": args.send_timeout,
        "slugs": entries,
        "summary": {"total": len(specs), "submitted": submitted, "failed": failed, "verify_passed": verify_passed},
    }


def build_qa_manifest(
    args: argparse.Namespace,
    batch_id: str,
    template_path: Path | None,
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "batch_id": batch_id,
        "operation": "qa",
        "qa_mode": args.qa_mode,
        "qa_template_file": str(template_path) if template_path else None,
        "created_at": utc_now(),
        "model": args.model,
        "agent": args.agent,
        "group_size": args.group_size,
        "groups": groups,
        "summary": {
            "total_groups": len(groups),
            "total_slugs": sum(len(group.get("slugs", [])) for group in groups),
            "submitted": sum(1 for group in groups if group.get("status") in {"submitted", "completed", "dry_run", "submitted_unconfirmed", "submitted_timeout"}),
            "failed": sum(1 for group in groups if group.get("status") == "failed"),
        },
    }


def run(
    args: argparse.Namespace,
    client_factory: Callable[[], Any] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    batch_id = args.batch_id or uuid.uuid4().hex[:8]
    paths = make_batch_paths(args.output_root, batch_id)
    ensure_batch_dirs(paths)

    if args.model is None:
        args.model = default_model()
    if args.model_mode is None:
        args.model_mode = DEFAULT_MODEL_MODE
    if args.agent is None:
        args.agent = default_agent()
    if args.title_pattern is None:
        args.title_pattern = "batch-qa-{batch_id}-group-{group_index}" if args.operation == "qa" else "batch-{batch_id}-{slug}"
    if not args.title_pattern.startswith("batch-"):
        raise SystemExit("--title-pattern must start with 'batch-'")

    template_text, template_path = resolve_template(args)
    custom_vars = parse_vars(args.var)

    client = None
    if not args.dry_run:
        client = (client_factory or OpenCodeClient)()

    if args.operation == "submit":
        specs = discover_specs(args.specs)
        requested_slugs = split_slugs(args.slugs)
        if requested_slugs:
            requested = set(requested_slugs)
            specs = [item for item in specs if item.slug in requested]
        if args.smoke_slug:
            specs = [item for item in specs if item.slug == args.smoke_slug]
        if not specs:
            raise ValueError("No specs found for submit operation")
        entries: list[dict[str, Any]] = []
        for index, item in enumerate(specs):
            timestamp = utc_now()
            title = title_for_submit(args.title_pattern, batch_id, item.slug, timestamp)
            rendered_path = paths.templates / f"rendered_{item.slug}.md"
            log_path = paths.slug_logs / f"{item.slug}.log"
            prompt = render_template(template_text, {
                "SLUG": item.slug,
                "TARGET_SLUG": item.slug,
                "NODE_SPEC_FILE": item.path,
                "SPEC_PATH": item.path,
                "DESTINATION_SPEC_FILE": item.path,
                "WAVE_DIR": paths.root,
                "BATCH_ID": batch_id,
                "TIMESTAMP": timestamp,
                **custom_vars,
            })
            rendered_path.write_text(prompt, encoding="utf-8")
            entry: dict[str, Any] = {
                "slug": item.slug,
                "spec_path": str(item.path),
                "status": "dry_run" if args.dry_run else "pending",
                "session_id": None,
                "title": title,
                "submitted_at": timestamp,
                "log_path": str(log_path.relative_to(paths.root)),
                "rendered_prompt_path": str(rendered_path.relative_to(paths.root)),
            }
            if not args.dry_run:
                assert client is not None
                session_id, status = submit_prompt_with_timeout(
                    client,
                    title=title,
                    prompt=prompt,
                    model=args.model,
                    provider=args.provider,
                    agent=args.agent,
                    wait=args.wait,
                    send_timeout=args.send_timeout,
                )
                entry["session_id"] = session_id
                entry["status"] = status if session_id else "failed"
                if args.verify:
                    entry["verify"] = verify_session(client, session_id)
            entries.append(entry)
            if index < len(specs) - 1 and not args.dry_run:
                sleep_fn(args.rate_limit)
        manifest = build_submit_manifest(args, batch_id, template_path, specs, entries)
        paths.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.dry_run:
            print(json.dumps({"batch_id": batch_id, "operation": "submit", "total": len(entries), "manifest": str(paths.manifest)}, ensure_ascii=False))
        return 0 if manifest["summary"]["failed"] == 0 else 2

    slugs = split_slugs(args.slugs)
    slugs.extend(slugs_from_manifest(args.slugs_from_manifest))
    slugs = list(dict.fromkeys(slugs))
    if not slugs:
        raise ValueError("QA operation requires --slugs or --slugs-from-manifest")
    grouped = group_slugs(slugs, args.group_size)
    groups: list[dict[str, Any]] = []
    for index, group in enumerate(grouped):
        timestamp = utc_now()
        group_id = f"group_{index}"
        title = title_for_group(args.title_pattern, batch_id, index, len(grouped))
        report_path = Path(args.qa_report).expanduser().resolve() if args.qa_report and len(grouped) == 1 else paths.groups / f"{group_id}_report.md"
        rendered_path = paths.templates / f"rendered_{group_id}.md"
        prompt = render_template(template_text, {
            "GROUP_SLUGS": ",".join(group),
            "GROUP_INDEX": index,
            "GROUP_COUNT": len(grouped),
            "DESTINATION_ROOT": args.destination_root or "",
            "QA_MODE": args.qa_mode,
            "QA_REPORT_PATH": report_path,
            "BATCH_ID": batch_id,
            "TIMESTAMP": timestamp,
            **custom_vars,
        })
        rendered_path.write_text(prompt, encoding="utf-8")
        qa_entry: dict[str, Any] = {
            "group_id": group_id,
            "slugs": group,
            "session_id": None,
            "status": "dry_run" if args.dry_run else "pending",
            "submitted_at": timestamp,
            "title": title,
            "qa_report_path": str(report_path.relative_to(paths.root)) if report_path.is_relative_to(paths.root) else str(report_path),
            "rendered_prompt_path": str(rendered_path.relative_to(paths.root)),
            "issue_count": None,
        }
        if not args.dry_run:
            assert client is not None
            session_id, status = submit_prompt_with_timeout(
                client,
                title=title,
                prompt=prompt,
                model=args.model,
                provider=args.provider,
                agent=args.agent,
                wait=args.wait,
                send_timeout=args.send_timeout,
            )
            qa_entry["session_id"] = session_id
            qa_entry["status"] = status if session_id else "failed"
            if args.wait and session_id:
                qa_entry["completed_at"] = utc_now()
        groups.append(qa_entry)
        if index < len(grouped) - 1 and not args.dry_run:
            sleep_fn(args.rate_limit)
    manifest = build_qa_manifest(args, batch_id, template_path, groups)
    paths.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.dry_run:
        print(json.dumps({"batch_id": batch_id, "operation": "qa", "total_groups": len(groups), "manifest": str(paths.manifest)}, ensure_ascii=False))
    return 0 if manifest["summary"]["failed"] == 0 else 2


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--operation", choices=("submit", "qa"))
    sub = parser.add_subparsers(dest="operation_cmd")
    submit = sub.add_parser("submit", help="render and submit one prompt per spec")
    qa = sub.add_parser("qa", help="render and submit one QA prompt per slug group")
    for target in (submit, qa):
        _add_common_arguments(target)
    _add_submit_arguments(submit)
    _add_qa_arguments(qa)


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--model")
    parser.add_argument("--model-mode", choices=("custom", "single_agent", "controller"))
    parser.add_argument("--provider")
    parser.add_argument("--agent")
    parser.add_argument("--title-pattern")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rate-limit", type=float, default=DEFAULT_RATE_LIMIT_SECONDS)
    parser.add_argument("--send-timeout", type=float, default=DEFAULT_SEND_TIMEOUT_SECONDS)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--batch-id")
    parser.add_argument("--var", action="append", default=[])
    parser.add_argument("--template-dir")


def _add_submit_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--template")
    parser.add_argument("--specs")
    parser.add_argument("--slugs")
    parser.add_argument("--smoke-slug")
    parser.add_argument("--verify", action="store_true")


def _add_qa_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--slugs")
    parser.add_argument("--slugs-from-manifest")
    parser.add_argument("--destination-root")
    parser.add_argument("--group-size", type=int, default=DEFAULT_GROUP_SIZE)
    parser.add_argument("--qa-template")
    parser.add_argument("--qa-mode", choices=("fix_small_issues", "audit_only"), default="fix_small_issues")
    parser.add_argument("--qa-report")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit OpenCode jobs in batches.")
    add_arguments(parser)
    return parser


def normalize_operation(args: argparse.Namespace) -> None:
    if getattr(args, "operation_cmd", None):
        args.operation = args.operation_cmd
    if not getattr(args, "operation", None):
        raise ValueError("batch requires an operation: submit or qa")


def validate_args(args: argparse.Namespace) -> None:
    normalize_operation(args)
    if args.rate_limit < 0:
        raise ValueError("--rate-limit must be >= 0")
    if args.send_timeout < 0:
        raise ValueError("--send-timeout must be >= 0")
    if args.operation == "submit":
        if not args.template and not args.template_dir:
            raise ValueError("submit operation requires --template or --template-dir")
        if not args.specs:
            raise ValueError("submit operation requires --specs")
    if args.operation == "qa":
        if args.group_size < 1:
            raise ValueError("--group-size must be >= 1")
        if not args.slugs and not args.slugs_from_manifest:
            raise ValueError("qa operation requires --slugs or --slugs-from-manifest")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_args(args)
        return run(args)
    except Exception as exc:
        print(f"Error: {exc}")
        return 1
