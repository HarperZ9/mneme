"""Falsifiers for copies of the store outside the database file.

Known copies that still hold erased text keep the receipt from saying
`erased`: replay snapshots older mneme left in the OS temp directory, and
snapshots of other stores under the snapshot root. Orphaned snapshots are
swept first. A foreign file in a shared temp directory is never read past a
size cap; on POSIX it is also never read through a link or when another user
owns it (those tests skip on Windows). Every text is planted test data.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mneme import AgentMemory
from mneme.cli import main

SENTENCE = "I live in Denver near the park."


def _db(tmp_path):
    db = tmp_path / "mneme.db"
    memory = AgentMemory(db)
    memory.remember("s", [{"id": "t1", "role": "user", "text": SENTENCE},
                          {"id": "t2", "role": "user", "text": "I prefer green tea daily."}])
    atom = next(r["id"] for r in memory.store.memories(layer="L1") if "Denver" in r["text"])
    memory.close()
    return db, atom


def _copy_bytes() -> bytes:
    return b"SQLite format 3\x00" + b"\x00" * 40 + SENTENCE.encode("utf-8") + b"\x00" * 40



def _out_of_reach(receipt, name):
    return next(item for item in receipt["out_of_reach"] if item["class"] == name)


def test_a_legacy_temp_snapshot_holding_the_text_means_copies_remain(tmp_path, capsys):
    (Path(tempfile.gettempdir()) / "mneme-replay-1-old.db").write_bytes(_copy_bytes())
    db, atom = _db(tmp_path)

    rc = main(["--state", str(db), "forget", atom, "--yes", "--reason", "user asked"])

    receipt = json.loads(capsys.readouterr().out)
    assert rc == 3
    assert receipt["status"] == "erased_copies_remain"
    assert _out_of_reach(receipt, "legacy_temp_snapshots")["containing_erased_text"] == 1


def test_an_orphan_is_swept_and_another_stores_snapshot_is_scanned(tmp_path, snapshot_root,
                                                                  dead_pid):
    orphan = snapshot_root / "unkeyed" / ("c" * 64) / f"mneme-replay-{dead_pid}-x.db"
    live = snapshot_root / ("st_" + "1" * 32) / f"mneme-replay-{os.getpid()}-y.db"
    db, atom = _db(tmp_path)
    memory = AgentMemory(db)
    for path in (orphan, live):                          # after the open-time sweep
        path.parent.mkdir(parents=True)
        path.write_bytes(_copy_bytes())
    live_before = live.exists()

    receipt = memory.forget(atom, reason="user asked")

    assert live_before and not orphan.exists()
    assert receipt["status"] == "erased_copies_remain"
    other = _out_of_reach(receipt, "other_snapshots")
    assert other["count"] == 1 and other["containing_erased_text"] == 1
    memory.close()


def test_a_copy_over_the_size_cap_is_not_read_and_the_receipt_is_incomplete(
        tmp_path, monkeypatch):
    import mneme.erase_outside as erase_outside

    monkeypatch.setattr(erase_outside, "MAX_COPY_BYTES", 16)
    (Path(tempfile.gettempdir()) / "mneme-replay-1-old.db").write_bytes(_copy_bytes())
    db, atom = _db(tmp_path)
    memory = AgentMemory(db)

    receipt = memory.forget(atom, reason="user asked")

    legacy = _out_of_reach(receipt, "legacy_temp_snapshots")
    assert legacy["not_scanned"] == 1 and legacy["containing_erased_text"] == 0
    assert receipt["status"] == "incomplete"
    memory.close()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO")
def test_a_fifo_in_the_shared_temp_directory_is_skipped_without_blocking(tmp_path):
    os.mkfifo(Path(tempfile.gettempdir()) / "mneme-replay-1-fifo.db")
    db, atom = _db(tmp_path)
    memory = AgentMemory(db)

    receipt = memory.forget(atom, reason="user asked")

    assert receipt["status"] == "erased"
    memory.close()


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX owners")
def test_a_file_another_user_owns_is_skipped(tmp_path, monkeypatch):
    (Path(tempfile.gettempdir()) / "mneme-replay-1-old.db").write_bytes(_copy_bytes())
    db, atom = _db(tmp_path)
    memory = AgentMemory(db)
    real = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real + 1)

    receipt = memory.forget(atom, reason="user asked")

    monkeypatch.undo()
    legacy = _out_of_reach(receipt, "legacy_temp_snapshots")
    assert legacy["skipped"] == 1 and legacy["containing_erased_text"] == 0
    memory.close()


def test_model_providers_are_named_out_of_reach(tmp_path):
    db, atom = _db(tmp_path)
    memory = AgentMemory(db)

    receipt = memory.forget(atom, reason="user asked")

    assert "provider" in _out_of_reach(receipt, "model_providers")["note"]
    memory.close()
