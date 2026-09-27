"""Falsifiers for replay snapshots in the per-user state directory.

A replay snapshot is a full copy of a memory database. It must never land
beside the database or in the shared OS temp directory, it must be findable by
the store it copies, and erase must be able to remove it. A snapshot left by a
process that died is swept on the next snapshot.
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile

from mneme import AgentMemory
from mneme.snapshot_dir import STORE_ID_PATTERN, store_id_of, sweep_orphans

TURNS = [{"id": "t1", "role": "user", "text": "My name is Dana and I live in Denver."}]


def _db(tmp_path, name="mneme.db"):
    db = tmp_path / name
    memory = AgentMemory(db)
    memory.remember("s", TURNS)
    memory.close()
    return db


def _store_id(path) -> str | None:
    conn = sqlite3.connect(path)
    try:
        return store_id_of(conn)
    finally:
        conn.close()


def test_a_writable_store_gets_a_random_stable_store_id(tmp_path):
    first, second = _db(tmp_path, "a.db"), _db(tmp_path, "b.db")
    before = _store_id(first)

    AgentMemory(first).close()                            # a writable reopen

    assert re.fullmatch(STORE_ID_PATTERN, before)
    assert _store_id(first) == before
    assert _store_id(second) != before


def test_a_replay_snapshot_lands_under_its_store_id_never_beside_the_database(
        tmp_path, snapshot_root, dead_pid):
    db = _db(tmp_path)
    reader = AgentMemory(db, read_only=True, immutable_snapshot=True)
    path = reader.store.private_snapshot_path

    assert path.parent == snapshot_root / _store_id(db)
    assert path.parent != db.parent
    assert list(tmp_path.glob("mneme-replay-*")) == []
    assert reader.close() is None
    assert not path.exists()


def test_erase_removes_a_snapshot_whose_finalizer_never_ran(tmp_path, snapshot_root):
    db = _db(tmp_path)
    reader = AgentMemory(db, read_only=True, immutable_snapshot=True)
    left_behind = reader.store.private_snapshot_path
    reader.store._private_finalizer.detach()              # the cleanup never runs
    reader.store.conn.close()
    assert left_behind.exists()

    writer = AgentMemory(db)
    atom = writer.store.memories(layer="L1")[0]["id"]
    receipt = writer.forget(atom, reason="user asked")

    assert not left_behind.exists()
    assert receipt["snapshots"]["removed"] == 1
    writer.close()



def test_the_sweep_removes_snapshots_of_dead_processes_and_keeps_live_ones(
        tmp_path, snapshot_root, dead_pid):
    store_dir = snapshot_root / ("st_" + "0" * 32)
    store_dir.mkdir(parents=True)
    dead = store_dir / f"mneme-replay-{dead_pid}-abc.db"
    live = store_dir / f"mneme-replay-{os.getpid()}-def.db"
    unknown = store_dir / "mneme-replay-legacyname.db"
    for path in (dead, live, unknown):
        path.write_bytes(b"x")
    (store_dir / f"{dead.name}-journal").write_bytes(b"j")

    result = sweep_orphans(snapshot_root)

    assert not dead.exists() and not (store_dir / f"{dead.name}-journal").exists()
    assert live.exists() and unknown.exists()
    assert result["removed"] == 1 and result["kept_unknown"] == 1


def test_a_hostile_store_id_cannot_steer_the_snapshot_path(tmp_path, snapshot_root):
    db = _db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value=? WHERE key='store_id'", ("../../escape",))
    conn.commit()
    conn.close()

    reader = AgentMemory(db, read_only=True, immutable_snapshot=True)
    path = reader.store.private_snapshot_path

    assert path.parent.parent == snapshot_root / "unkeyed"
    assert snapshot_root in path.parents
    reader.close()


def test_legacy_temp_snapshots_are_counted_and_left_in_place(tmp_path, monkeypatch):
    legacy_dir = tmp_path / "os-temp"
    legacy_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(legacy_dir))
    with_text = legacy_dir / "mneme-replay-old1.db"
    without = legacy_dir / "mneme-replay-old2.db"
    with_text.write_bytes(b"\x00" * 16 + TURNS[0]["text"].encode("utf-8"))
    without.write_bytes(b"\x00" * 64)
    memory = AgentMemory(":memory:")
    memory.remember("s", TURNS)
    atom = memory.store.memories(layer="L1")[0]["id"]

    receipt = memory.forget(atom, reason="user asked")

    legacy = next(item for item in receipt["out_of_reach"]
                  if item["class"] == "legacy_temp_snapshots")
    assert legacy["count"] == 2 and legacy["containing_erased_text"] == 1
    assert with_text.exists() and without.exists()
