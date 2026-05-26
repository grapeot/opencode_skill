from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import migrate, query, selector

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
