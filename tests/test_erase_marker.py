"""Falsifiers for the unfinished-erase marker and the order of snapshot removal.

The marker (`meta.erase_pending`) must stay while any scrub step or snapshot
removal did not finish, so `mneme status` and `mneme doctor` keep warning
until `mneme scrub` finishes. A refused erase must change nothing, snapshots
included. A scrub that finishes an interrupted erase says the residual scan
did not run. Every text is planted test data.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

import mneme.erase as erase_module
from mneme import AgentMemory
from mneme.cli import main
from mneme.erase import (ErasedTextInReasonError, Selection, StalePlanError,
                         apply_erase, plan_erase)
from mneme.schema import META_ERASE_PENDING
from mneme.state_report import describe

TURNS = [{"id": "t1", "role": "user", "text": "I live in Denver near the park."},
         {"id": "t2", "role": "user", "text": "I prefer green tea daily."}]


def _db(tmp_path, *, wal: bool = False):
    db = tmp_path / "mneme.db"
    memory = AgentMemory(db)
    if wal:
        memory.store.conn.execute("PRAGMA journal_mode=WAL")
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


def test_a_busy_scrub_keeps_the_marker_until_mneme_scrub_finishes(tmp_path, monkeypatch,
                                                                   capsys):
    monkeypatch.setattr(erase_module, "CHECKPOINT_RETRY_SECONDS", 0.0)
    db, atom = _db(tmp_path, wal=True)
    reader = sqlite3.connect(db)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM turns").fetchone()     # pins the old pages
    memory = AgentMemory(db)

    receipt = memory.forget(atom, reason="user asked")
    memory.close()

    assert "scrub_incomplete" in receipt["findings"]
    assert _pending(db)
    assert any("mneme scrub" in w for w in describe(str(db), check="doctor")["warnings"])
    reader.rollback()
    reader.close()
    assert main(["--state", str(db), "scrub"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["erase_pending"] == "cleared"
    assert result["residual_scan"] == "ran_at_erase"
    assert not _pending(db)


def test_a_failed_snapshot_removal_keeps_the_marker(tmp_path, monkeypatch):
    import mneme.snapshot_dir as snapshot_dir

    db, atom = _db(tmp_path)
    _left_snapshot(db)
    monkeypatch.setattr(snapshot_dir, "_remove_with_sidecars", lambda path: False)
    memory = AgentMemory(db)

    receipt = memory.forget(atom, reason="user asked")
    memory.close()

    assert "snapshot_removal_failed" in receipt["findings"]
    assert _pending(db)


def test_a_clean_erase_clears_the_marker(tmp_path):
    db, atom = _db(tmp_path)
    memory = AgentMemory(db)

    receipt = memory.forget(atom, reason="user asked")
    memory.close()

    assert receipt["status"] == "erased"
    assert not _pending(db)


@pytest.mark.parametrize("digest, reason, error", [
    ("0" * 64, "user asked", StalePlanError),
    (None, "remove I live in Denver near the park.", ErasedTextInReasonError),
])
def test_a_refused_erase_leaves_the_stores_snapshots(tmp_path, digest, reason, error):
    db, atom = _db(tmp_path)
    left = _left_snapshot(db)
    memory = AgentMemory(db)
    selection = Selection(memories=(atom,))
    expected = digest or plan_erase(memory.store, selection)["plan_sha256"]

    with pytest.raises(error):
        apply_erase(memory.store, selection, expected, reason=reason)
    memory.close()

    assert left.exists()


def test_scrub_after_an_interrupted_erase_says_the_scan_did_not_run(tmp_path, monkeypatch,
                                                                    capsys):
    db, atom = _db(tmp_path)

    def interrupted(_store):
        raise KeyboardInterrupt

    monkeypatch.setattr(erase_module, "scrub_store", interrupted)
    memory = AgentMemory(db)
    with pytest.raises(KeyboardInterrupt):
        memory.forget(atom, reason="user asked")
    memory.close()
    monkeypatch.undo()

    assert main(["--state", str(db), "scrub"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["erase_pending"] == "cleared"
    assert result["residual_scan"] == "not_run"
    assert "no residual scan" in result["residual_scan_note"]
    assert main(["--state", str(db), "scrub"]) == 0
    assert "residual_scan" not in json.loads(capsys.readouterr().out)
