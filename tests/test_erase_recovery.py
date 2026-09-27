"""Falsifiers for an erase that fails or stops after its commit.

Rows are gone once the erase commits. A failure after that point must not be
reported as a refusal, an interrupt must leave a marker that status, doctor
and `mneme scrub` act on, and no replay snapshot of the store may outlive it.
Every text is planted test data.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest

import mneme.erase as erase_module
import mneme.erase_receipt as erase_receipt
from mneme import AgentMemory
from mneme.cli import main
from mneme.schema import META_ERASE_PENDING
from mneme.state_report import describe

TURNS = [{"id": "t1", "role": "user", "text": "I live in Denver near the park."},
         {"id": "t2", "role": "user", "text": "I prefer green tea daily."}]


def _db(tmp_path):
    db = tmp_path / "mneme.db"
    memory = AgentMemory(db)
    memory.remember("s", TURNS)
    atom = next(r["id"] for r in memory.store.memories(layer="L1") if "Denver" in r["text"])
    memory.close()
    return db, atom


def _pending(db) -> bool:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT 1 FROM meta WHERE key=?",
                            (META_ERASE_PENDING,)).fetchone() is not None
    finally:
        conn.close()


def _left_snapshot(db):
    reader = AgentMemory(db, read_only=True, immutable_snapshot=True)
    path = reader.store.private_snapshot_path
    reader.store._private_finalizer.detach()
    reader.store.conn.close()
    return path


def test_a_failure_after_commit_is_reported_as_erased_unverified(tmp_path, monkeypatch, capsys):
    db, atom = _db(tmp_path)

    def locked(_conn):
        raise sqlite3.OperationalError("planted: database is locked")

    monkeypatch.setattr(erase_receipt, "kept_values", locked)
    rc = main(["--state", str(db), "forget", atom, "--yes", "--reason", "user asked"])

    out = capsys.readouterr()
    receipt = json.loads(out.out)
    assert rc == 3 and "refused" not in out.err
    assert receipt["status"] == "erased_unverified"
    assert receipt["error"] == "OperationalError"
    assert "mneme scrub" in receipt["remedy"]
    assert _pending(db)
    monkeypatch.undo()
    assert main(["--state", str(db), "scrub"]) == 0
    assert not _pending(db)


def test_an_interrupt_after_commit_leaves_a_marker_and_no_snapshot(tmp_path, monkeypatch, capsys):
    db, atom = _db(tmp_path)
    left = _left_snapshot(db)

    def interrupted(_store):
        raise KeyboardInterrupt

    monkeypatch.setattr(erase_module, "scrub_store", interrupted)
    memory = AgentMemory(db)
    with pytest.raises(KeyboardInterrupt):
        memory.forget(atom, reason="user asked")
    memory.close()
    monkeypatch.undo()

    assert not left.exists()
    assert _pending(db)
    doctor = describe(str(db), check="doctor")
    assert any("mneme scrub" in w for w in doctor["warnings"])
    assert main(["--state", str(db), "forget", atom, "--yes"]) == 2
    assert "mneme scrub" in capsys.readouterr().err
    assert main(["--state", str(db), "scrub"]) == 0
    assert not _pending(db)


def test_scrub_removes_the_stores_snapshots(tmp_path, capsys):
    db, _atom = _db(tmp_path)
    left = _left_snapshot(db)

    assert main(["--state", str(db), "scrub"]) == 0

    assert json.loads(capsys.readouterr().out)["snapshots"]["removed"] == 1
    assert not left.exists()


def _dead_pid() -> int:
    child = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                           capture_output=True, text=True, check=True)
    return int(child.stdout)


def test_opening_a_writable_store_sweeps_orphaned_snapshots(tmp_path, snapshot_root):
    db, _atom = _db(tmp_path)
    orphan = snapshot_root / "unkeyed" / ("a" * 64) / f"mneme-replay-{_dead_pid()}-x.db"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"planted orphan copy")

    AgentMemory(db).close()

    assert not orphan.exists()


def test_the_mcp_server_sweeps_orphans_when_it_starts(snapshot_root):
    from mneme.mcp import serve

    orphan = snapshot_root / ("st_" + "b" * 32) / f"mneme-replay-{_dead_pid()}-x.db"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"planted orphan copy")

    assert serve(stdin=iter(()), stdout=open(os.devnull, "w")) == 0

    assert not orphan.exists()


def test_a_snapshot_removed_while_another_process_lives_is_a_remaining_copy(
        tmp_path, monkeypatch):
    import mneme.snapshot_dir as snapshot_dir

    monkeypatch.setattr(snapshot_dir, "UNLINK_KEEPS_OPEN_FILES", True)
    db, atom = _db(tmp_path)
    memory = AgentMemory(db)
    directory = snapshot_dir.store_dir(snapshot_dir.store_id_of(memory.store.conn), db)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"mneme-replay-{os.getppid()}-x.db").write_bytes(b"planted live copy")

    receipt = memory.forget(atom, reason="user asked")

    assert receipt["snapshots"]["removed_while_live"] == 1
    assert receipt["status"] == "erased_copies_remain"
    memory.close()
