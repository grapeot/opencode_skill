from __future__ import annotations

import json
import sqlite3

import pytest

from opencode_skill import migrate, query, selector


def _seed_assistant_messages(db_path, prefix: str, base_ts: int, n: int):
    """Add n assistant messages with predictable token counts to db."""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    # Need a session to satisfy FK
    sid = f"sess_{prefix}"
    cur.execute(
        "INSERT OR IGNORE INTO project (id, worktree, vcs, time_created, time_updated, sandboxes) VALUES (?,?,?,?,?,?)",
        ("Pq", "/tmp", "git", base_ts, base_ts, "[]"),
    )
    cur.execute(
        "INSERT OR IGNORE INTO session (id, project_id, slug, directory, title, version, time_created, time_updated) VALUES (?,?,?,?,?,?,?,?)",
        (sid, "Pq", sid, "/tmp", "title", "0.0.0", base_ts, base_ts),
    )
    for i in range(n):
        ts = base_ts + i * 1000
        data = {
            "role": "assistant",
            "providerID": f"provider_{prefix}",
            "modelID": f"model_{prefix}",
            "tokens": {
                "input": 10,
                "output": 20,
                "reasoning": 5,
                "cache": {"read": 100, "write": 50},
            },
            "time": {"created": ts},
        }
        cur.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
            (f"m_{prefix}_{i}", sid, ts, ts, json.dumps(data)),
        )
    conn.commit()
    conn.close()


def test_iter_assistant_messages_main_only(tmp_path, empty_template_db):
    main = tmp_path / "main.db"
    import shutil
    shutil.copyfile(empty_template_db, main)
    _seed_assistant_messages(main, "main", base_ts=1000_000, n=3)

    msgs = list(query.iter_assistant_messages(main_db=main, archive_dbs=[]))
    assert len(msgs) == 3
    m = msgs[0]
    assert m.tokens_input == 10
    assert m.tokens_output == 20
    assert m.tokens_reasoning == 5
    assert m.tokens_cache_read == 100
    assert m.tokens_cache_write == 50
    assert m.provider == "provider_main"
    assert m.model == "model_main"
    assert m.source_db == "main"


def test_iter_assistant_messages_with_archive_attaches(tmp_path, empty_template_db):
    main = tmp_path / "main.db"
    arch = tmp_path / "arch.db"
    import shutil
    shutil.copyfile(empty_template_db, main)
    shutil.copyfile(empty_template_db, arch)
    _seed_assistant_messages(main, "main", base_ts=2_000_000, n=2)
    _seed_assistant_messages(arch, "arch", base_ts=1_000_000, n=4)

    msgs = list(query.iter_assistant_messages(main_db=main, archive_dbs=[arch]))
    # 2 from main + 4 from archive = 6 total (token analytics needs both)
    assert len(msgs) == 6
    sources = {m.source_db for m in msgs}
    assert sources == {"main", "arch.db"}


def test_iter_assistant_messages_include_archive_false(tmp_path, empty_template_db):
    main = tmp_path / "main.db"
    arch = tmp_path / "arch.db"
    import shutil
    shutil.copyfile(empty_template_db, main)
    shutil.copyfile(empty_template_db, arch)
    _seed_assistant_messages(main, "main", base_ts=2_000_000, n=2)
    _seed_assistant_messages(arch, "arch", base_ts=1_000_000, n=4)

    # Export pipeline: only main, archive ignored
    msgs = list(query.iter_assistant_messages(
        main_db=main, archive_dbs=[arch], include_archive=False
    ))
    assert len(msgs) == 2
    assert all(m.source_db == "main" for m in msgs)


def test_iter_time_window(tmp_path, empty_template_db):
    main = tmp_path / "main.db"
    import shutil
    shutil.copyfile(empty_template_db, main)
    _seed_assistant_messages(main, "main", base_ts=10_000, n=10)

    msgs = list(query.iter_assistant_messages(
        since_ms=12_000, until_ms=15_000, main_db=main, archive_dbs=[]
    ))
    # ts: 10000, 11000, 12000, 13000, 14000, ... 19000 -> [12000, 15000) → 12k, 13k, 14k
    assert len(msgs) == 3


def test_iter_skips_missing_archive(tmp_path, empty_template_db):
    main = tmp_path / "main.db"
    missing = tmp_path / "does_not_exist.db"
    import shutil
    shutil.copyfile(empty_template_db, main)
    _seed_assistant_messages(main, "main", base_ts=0, n=2)

    msgs = list(query.iter_assistant_messages(main_db=main, archive_dbs=[missing]))
    assert len(msgs) == 2
    assert all(m.source_db == "main" for m in msgs)


def test_stats_reports_each_db(tmp_path, empty_template_db):
    main = tmp_path / "main.db"
    arch = tmp_path / "arch.db"
    missing = tmp_path / "no.db"
    import shutil
    shutil.copyfile(empty_template_db, main)
    shutil.copyfile(empty_template_db, arch)

    s = query.stats(main_db=main, archive_dbs=[arch, missing])
    labels = {db["label"]: db for db in s["databases"]}
    assert labels["main"]["exists"]
    assert labels["arch.db"]["exists"]
    assert labels["no.db"]["exists"] is False
    # both seeded DBs have all 4 tables (and 0 rows)
    assert labels["main"]["counts"]["session"] == 0
    assert labels["main"]["counts"]["project"] == 0


def test_iter_skips_malformed_data(tmp_path, empty_template_db):
    """Bad JSON in message.data must not crash analytics."""
    main = tmp_path / "main.db"
    import shutil
    shutil.copyfile(empty_template_db, main)
    conn = sqlite3.connect(str(main))
    conn.execute(
        "INSERT INTO project (id, worktree, vcs, time_created, time_updated, sandboxes) VALUES (?,?,?,?,?,?)",
        ("P", "/tmp", "git", 0, 0, "[]"),
    )
    conn.execute(
        "INSERT INTO session (id, project_id, slug, directory, title, version, time_created, time_updated) VALUES (?,?,?,?,?,?,?,?)",
        ("S", "P", "s", "/tmp", "t", "0", 0, 0),
    )
    # one bad JSON, one missing tokens, one valid
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
        ("m1", "S", 0, 0, "not json"),
    )
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
        ("m2", "S", 1, 1, json.dumps({"role": "assistant"})),  # no tokens
    )
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
        (
            "m3", "S", 2, 2,
            json.dumps({"role": "assistant", "tokens": {"input": 1, "output": 2}}),
        ),
    )
    conn.commit()
    conn.close()

    msgs = list(query.iter_assistant_messages(main_db=main, archive_dbs=[]))
    # m1 dropped (bad json), m2 yielded with zero tokens, m3 with real tokens
    assert len(msgs) == 2
    by_id = {m.id: m for m in msgs}
    assert by_id["m2"].tokens_input == 0
    assert by_id["m3"].tokens_input == 1
