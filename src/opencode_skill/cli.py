from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import batch, export, migrate, query, selector
from .client import OpenCodeClient
from .jobs import DryRunVerificationError, append_job, read_prompt, submit_dry_run, submit_job

def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        _ = os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    if not value:
        return default
    return Path(value).expanduser()


_load_dotenv(Path.cwd() / ".env")

_OC = Path.home() / ".local" / "share" / "opencode"
DEFAULT_MAIN = _env_path("OPENCODE_MAIN_DB", _OC / "opencode.db")
DEFAULT_ARCHIVE = _env_path("OPENCODE_ARCHIVE_DB", _OC / "opencode_archive.db")
DEFAULT_OLD_ARCHIVE = _env_path("OPENCODE_OLD_ARCHIVE_DB", _OC / "archive" / "old_sessions.db")
TEMPLATE_DB = _env_path(
    "OPENCODE_EMPTY_TEMPLATE_DB",
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "empty_opencode.db",
)


def cmd_stats(args: argparse.Namespace) -> int:
    archive_dbs = [args.archive, args.old_archive] if not args.json_out else [args.archive, args.old_archive]
    s = query.stats(main_db=args.main, archive_dbs=archive_dbs)
    if args.json_out:
        print(json.dumps(s, indent=2))
        return 0
    for db in s["databases"]:
        if not db["exists"]:
            print(f"  {db['label']:<28}  (missing) {db['path']}")
            continue
        c = db["counts"]
        size = db["size_bytes"] / 1e9
        print(
            f"  {db['label']:<28}  {size:>5.2f} GB  "
            f"sessions={c['session']:>6}  "
            f"messages={c['message']:>7}  "
            f"parts={c['part']:>8}"
        )
    return 0


def _build_selector(args: argparse.Namespace) -> "selector.Selector":
    if args.selector == "ids":
        return selector.from_id_file(Path(args.file))
    if args.selector == "title":
        return selector.from_title_prefix(args.prefix)
    if args.selector == "time":
        cutoff_ms = _parse_cutoff_ms(args.before)
        # Build: roots older than cutoff, then expand descendants.
        # Implemented inline since we need a SQL where on parent_id IS NULL.
        return _SqlSelector(
            "parent_id IS NULL AND time_created < ?",
            (cutoff_ms,),
            f"roots older than {args.before}",
        )
    raise SystemExit(f"unknown selector: {args.selector}")


class _SqlSelector(selector.Selector):
    pass


def _parse_cutoff_ms(spec: str) -> int:
    """Accept either '30d', '2026-04-09', or 'YYYY-MM-DDTHH:MM:SS'."""
    if spec.endswith("d") and spec[:-1].isdigit():
        days = int(spec[:-1])
        cutoff = datetime.now() - timedelta(days=days)
    else:
        try:
            cutoff = datetime.fromisoformat(spec)
        except ValueError:
            raise SystemExit(f"cannot parse cutoff: {spec}")
    return int(cutoff.timestamp() * 1000)


def _resolve_with_descendants(args: argparse.Namespace, sel) -> "selector.Selector":
    if args.no_expand:
        return sel
    return selector.expand_with_descendants(args.main, sel)


def cmd_plan(args: argparse.Namespace) -> int:
    sel = _resolve_with_descendants(args, _build_selector(args))
    p = migrate.plan(args.main, sel)
    print(f"selector: {sel.description}  (after expand: {len(sel.params)} ids)")
    print(f"  sessions: {p.session_count}")
    print(f"  messages: {p.message_count}")
    print(f"  parts:    {p.part_count}")
    if p.sample_titles:
        print(f"  sample titles:")
        for t in p.sample_titles[:5]:
            print(f"    - {t[:80]}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("apply requires --confirm to actually run")
        return 2

    if not args.dest.exists():
        if not TEMPLATE_DB.exists():
            print(f"missing bootstrap template: {TEMPLATE_DB}", file=sys.stderr)
            return 3
        args.dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TEMPLATE_DB, args.dest)
        print(f"  initialized {args.dest} from bootstrap template")

    sel = _resolve_with_descendants(args, _build_selector(args))

    print(f"selector: {sel.description}  (after expand: {len(sel.params)} ids)")
    cp = migrate.copy(args.main, args.dest, sel)
    print(
        f"COPY: sessions={cp.sessions_inserted}  messages={cp.messages_inserted}  parts={cp.parts_inserted}"
    )

    v = migrate.verify(args.main, args.dest, sel)
    if not v.ok:
        print(f"VERIFY FAILED: {v.issues}", file=sys.stderr)
        print("  source DB unchanged. Inspect dest, fix, retry.")
        return 4
    print(
        f"VERIFY ok: src=({v.src_session_count},{v.src_message_count},{v.src_part_count})  "
        f"dst=({v.dst_session_count},{v.dst_message_count},{v.dst_part_count})"
    )

    if args.no_delete:
        print("(--no-delete given; skipping delete from source)")
        return 0

    d = migrate.delete_from_src(args.main, cp.session_ids)
    print(f"DELETE from main: {d}")
    return 0


def cmd_vacuum(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("vacuum-main requires --confirm")
        return 2
    db = args.main
    size_before = db.stat().st_size
    free_required = size_before
    free_avail = shutil.disk_usage(db.parent).free
    if free_avail < free_required:
        print(
            f"refuse: needs {free_required/1e9:.1f} GB free, have {free_avail/1e9:.1f} GB",
            file=sys.stderr,
        )
        return 3
    print(f"VACUUM {db} (size {size_before/1e9:.2f} GB)...")
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()
    size_after = db.stat().st_size
    print(f"done. {size_before/1e9:.2f} GB -> {size_after/1e9:.2f} GB")
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    prompt_text = read_prompt(prompt=args.prompt, prompt_file=args.prompt_file, use_stdin=args.stdin)
    client = OpenCodeClient()
    try:
        if args.dry_run:
            result = submit_dry_run(
                client,
                title=args.title,
                model=args.model,
                provider=args.provider,
                agent=args.agent,
                delete_session=not args.keep_dry_run_session,
                send_timeout=args.send_timeout,
                wait_poll_interval=args.wait_poll_interval,
                wait_max_seconds=args.wait_max_seconds,
            )
        else:
            result = submit_job(
                client,
                title=args.title,
                prompt=prompt_text,
                model=args.model,
                provider=args.provider,
                agent=args.agent,
                wait=args.wait and not args.no_wait,
                delete_session=args.delete_session,
                send_timeout=args.send_timeout if args.send_timeout is not None else (None if args.wait else 5.0),
                wait_poll_interval=args.wait_poll_interval,
                wait_max_seconds=args.wait_max_seconds,
            )
    except DryRunVerificationError as exc:
        print(f"dry run failed: {exc}", file=sys.stderr)
        return 2
    payload = {
        "session_id": result.session_id,
        "title": result.title,
        "status": result.status,
        "deleted": result.deleted,
        "wait_completed": result.wait_completed,
        "dry_run": result.dry_run,
    }
    if result.verification:
        payload["verification"] = result.verification
    if args.json_out:
        print(json.dumps(payload, indent=2))
    else:
        print(f"session_id: {result.session_id}")
        print(f"status: {result.status}")
        print(f"deleted: {str(result.deleted).lower()}")
        if result.dry_run:
            print(f"dry_run: true")
            print(f"verification: {result.verification}")
    return 0


def cmd_append(args: argparse.Namespace) -> int:
    prompt_text = read_prompt(prompt=args.prompt, prompt_file=args.prompt_file, use_stdin=args.stdin)
    client = OpenCodeClient()
    try:
        if args.dry_run:
            client.get_session_info(args.session_id)
            result = submit_dry_run(
                client,
                title=f"append {args.session_id}",
                model=args.model,
                provider=args.provider,
                agent=args.agent,
                delete_session=not args.keep_dry_run_session,
                send_timeout=args.send_timeout,
                wait_poll_interval=args.wait_poll_interval,
                wait_max_seconds=args.wait_max_seconds,
            )
        else:
            result = append_job(
                client,
                session_id=args.session_id,
                prompt=prompt_text,
                model=args.model,
                provider=args.provider,
                agent=args.agent,
                wait=args.wait,
                send_timeout=args.send_timeout if args.send_timeout is not None else (None if args.wait else 5.0),
                wait_poll_interval=args.wait_poll_interval,
                wait_max_seconds=args.wait_max_seconds,
            )
    except DryRunVerificationError as exc:
        print(f"dry run failed: {exc}", file=sys.stderr)
        return 2
    payload = {
        "session_id": result.session_id,
        "target_session_id": args.session_id,
        "status": result.status,
        "deleted": result.deleted,
        "wait_completed": result.wait_completed,
        "dry_run": result.dry_run,
    }
    if result.verification:
        payload["verification"] = result.verification
    if args.json_out:
        print(json.dumps(payload, indent=2))
    else:
        print(f"session_id: {result.session_id}")
        print(f"target_session_id: {args.session_id}")
        print(f"status: {result.status}")
        print(f"deleted: {str(result.deleted).lower()}")
        if result.dry_run:
            print("dry_run: true")
            print(f"verification: {result.verification}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    since_ms = _parse_cutoff_ms(args.since) if args.since else None
    try:
        result = export.export_sessions(
            args.main,
            args.out,
            since_ms=since_ms,
            skip_subagent=not args.include_subagent,
            dry_run=args.dry_run,
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    if args.json_out:
        print(json.dumps(result, indent=2))
    else:
        verb = "would export" if args.dry_run else "exported"
        print(f"scanned={result['scanned']}  {verb}={result['exported']}  out={args.out}")
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    try:
        batch.validate_args(args)
        return batch.run(args)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"batch failed: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="opencode-skill")
    p.add_argument("--main", type=Path, default=DEFAULT_MAIN, help="path to main OpenCode DB")
    p.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE, help="path to batch archive DB")
    p.add_argument(
        "--old-archive", type=Path, default=DEFAULT_OLD_ARCHIVE, help="path to >30d archive DB"
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    p_stats = sub.add_parser("stats", help="show row counts and sizes")
    p_stats.add_argument("--json", dest="json_out", action="store_true")
    p_stats.set_defaults(func=cmd_stats)

    common_sel = lambda parent: _add_selector_args(parent)

    p_plan = sub.add_parser("plan", help="dry run: print what apply would do")
    common_sel(p_plan)
    p_plan.set_defaults(func=cmd_plan)

    p_apply = sub.add_parser("apply", help="copy + verify + delete")
    common_sel(p_apply)
    p_apply.add_argument("--dest", type=Path, default=DEFAULT_ARCHIVE, help="destination DB")
    p_apply.add_argument("--confirm", action="store_true")
    p_apply.add_argument("--no-delete", action="store_true", help="copy + verify, but skip delete")
    p_apply.set_defaults(func=cmd_apply)

    p_vac = sub.add_parser("vacuum-main", help="VACUUM main DB (requires server stopped)")
    p_vac.add_argument("--confirm", action="store_true")
    p_vac.set_defaults(func=cmd_vacuum)

    p_submit = sub.add_parser("submit", help="submit one prompt to an OpenCode server")
    p_submit.add_argument("prompt", nargs="?", help="prompt text; use --prompt-file or --stdin for private prompts")
    p_submit.add_argument("--prompt-file", type=Path, help="read prompt text from a UTF-8 file")
    p_submit.add_argument("--stdin", action="store_true", help="read prompt text from standard input")
    p_submit.add_argument("--title", default=os.environ.get("OPENCODE_TITLE", "OpenCode Job"))
    p_submit.add_argument("--model", default=os.environ.get("OPENCODE_MODEL", "example/default-model"))
    p_submit.add_argument("--provider", default=os.environ.get("OPENCODE_PROVIDER"))
    p_submit.add_argument("--agent", default=os.environ.get("OPENCODE_AGENT"))
    p_submit.add_argument("--dry-run", action="store_true", help="submit a harmless OK-only prompt instead of the provided prompt")
    p_submit.add_argument(
        "--keep-dry-run-session",
        action="store_true",
        help="preserve the ephemeral dry-run session instead of deleting it after verification",
    )
    p_submit.add_argument("--wait", action="store_true", help="block until the OpenCode session is no longer running")
    p_submit.add_argument("--no-wait", action="store_true", help="deprecated compatibility flag; submit already returns after handoff by default")
    p_submit.add_argument("--delete-session", action="store_true", help="delete the session after submission/wait completes")
    p_submit.add_argument("--send-timeout", type=float, default=None)
    p_submit.add_argument("--wait-poll-interval", type=float, default=15.0)
    p_submit.add_argument("--wait-max-seconds", type=float, default=7200.0)
    p_submit.add_argument("--json", dest="json_out", action="store_true")
    p_submit.set_defaults(func=cmd_submit)

    p_append = sub.add_parser("append", help="append one prompt to an existing OpenCode session")
    p_append.add_argument("prompt", nargs="?", help="prompt text; use --prompt-file or --stdin for private prompts")
    p_append.add_argument("--prompt-file", type=Path, help="read prompt text from a UTF-8 file")
    p_append.add_argument("--stdin", action="store_true", help="read prompt text from standard input")
    p_append.add_argument("--session-id", required=True, help="existing OpenCode session id to append to")
    p_append.add_argument("--model", default=os.environ.get("OPENCODE_MODEL", "example/default-model"))
    p_append.add_argument("--provider", default=os.environ.get("OPENCODE_PROVIDER"))
    p_append.add_argument("--agent", default=os.environ.get("OPENCODE_AGENT"))
    p_append.add_argument("--dry-run", action="store_true", help="verify target session and routing without appending the real prompt")
    p_append.add_argument(
        "--keep-dry-run-session",
        action="store_true",
        help="preserve the ephemeral dry-run session instead of deleting it after verification",
    )
    p_append.add_argument("--wait", action="store_true", help="block until the OpenCode session is no longer running")
    p_append.add_argument("--send-timeout", type=float, default=None)
    p_append.add_argument("--wait-poll-interval", type=float, default=15.0)
    p_append.add_argument("--wait-max-seconds", type=float, default=7200.0)
    p_append.add_argument("--json", dest="json_out", action="store_true")
    p_append.set_defaults(func=cmd_append)

    p_export = sub.add_parser(
        "export", help="export OpenCode sessions to markdown (one file per session)"
    )
    p_export.add_argument(
        "--out", type=Path, required=True, help="output directory for markdown files"
    )
    p_export.add_argument(
        "--since",
        help="only sessions created after this cutoff (e.g. '30d' or '2026-04-09')",
    )
    p_export.add_argument(
        "--include-subagent",
        action="store_true",
        help="include subagent/system fan-out sessions (skipped by default)",
    )
    p_export.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be exported without writing files",
    )
    p_export.add_argument("--json", dest="json_out", action="store_true")
    p_export.set_defaults(func=cmd_export)

    p_batch = sub.add_parser("batch", help="render and submit batch OpenCode jobs")
    batch.add_arguments(p_batch)
    p_batch.set_defaults(func=cmd_batch)

    return p


def _add_selector_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--selector",
        choices=["ids", "title", "time"],
        required=True,
        help="how to pick sessions",
    )
    parser.add_argument("--file", help="for --selector ids: path to ids txt")
    parser.add_argument("--prefix", help="for --selector title: e.g. 'batch-'")
    parser.add_argument(
        "--before",
        help="for --selector time: cutoff (e.g. '30d' or '2026-04-09')",
    )
    parser.add_argument(
        "--no-expand",
        action="store_true",
        help="skip BFS expand to descendants (default: expand to keep subtree intact)",
    )


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
