"""Falsifiers for hostile snapshot directories and hostile database contents.

Before this change a snapshot directory that could not be listed read as
empty, so an erase said `erased` while a snapshot holding the text survived;
a snapshot name with an out-of-range process id could raise on open or be
read as a live process after truncation; and a malformed merge row stopped
every erase with a traceback. A link planted as a snapshot directory or a
snapshot file must never be followed. Every value is planted test data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import mneme.snapshot_dir as snapshot_dir
from mneme import AgentMemory
from mneme.cli import main
from mneme.os_facts import pid_alive

SENTENCE = "I live in Denver near the park."


def _db(tmp_path):
    db = tmp_path / "mneme.db"
    memory = AgentMemory(db)
    memory.remember("s", [{"id": "t1", "role": "user", "text": SENTENCE},
                          {"id": "t2", "role": "user", "text": "I prefer green tea daily."}])
    atom = next(r["id"] for r in memory.store.memories(layer="L1") if "Denver" in r["text"])
    store_id = snapshot_dir.store_id_of(memory.store.conn)
    memory.close()
    return db, atom, store_id


def test_a_snapshot_directory_that_cannot_be_listed_is_a_failed_removal(
        tmp_path, monkeypatch):
    db, atom, store_id = _db(tmp_path)
    directory = snapshot_dir.store_dir(store_id, db)
    directory.mkdir(parents=True)
    (directory / "mneme-replay-1-x.db").write_bytes(SENTENCE.encode("utf-8"))
    real = os.scandir

    def denied(path="."):
        if Path(path) == directory:
            raise PermissionError("planted: list denied")
        return real(path)

    monkeypatch.setattr(os, "scandir", denied)
    memory = AgentMemory(db)
    receipt = memory.forget(atom, reason="user asked")
    memory.close()

    assert receipt["snapshots"]["failed"] >= 1
    assert "snapshot_removal_failed" in receipt["findings"]
    assert receipt["status"] != "erased"


@pytest.mark.skipif(os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="POSIX permission bits, not as root")
def test_a_directory_without_list_permission_is_a_failed_removal(tmp_path):
    db, atom, store_id = _db(tmp_path)
    directory = snapshot_dir.store_dir(store_id, db)
    directory.mkdir(parents=True)
    (directory / "mneme-replay-1-x.db").write_bytes(SENTENCE.encode("utf-8"))
    directory.chmod(0o300)
    try:
        memory = AgentMemory(db)
        receipt = memory.forget(atom, reason="user asked")
        memory.close()
    finally:
        directory.chmod(0o700)

    assert "snapshot_removal_failed" in receipt["findings"]


@pytest.mark.parametrize("pid", ["99999999999999999999", str(2**32 + 4), "0"])
def test_an_out_of_range_pid_in_a_name_is_unknown_and_never_blocks(tmp_path, pid):
    db, atom, store_id = _db(tmp_path)
    directory = snapshot_dir.store_dir(store_id, db)
    directory.mkdir(parents=True)
    planted = directory / f"mneme-replay-{pid}-x.db"
    planted.write_bytes(b"planted")

    counts = snapshot_dir.store_snapshot_counts(store_id, db)
    memory = AgentMemory(db)                   # the open-time sweep must not raise
    receipt = memory.forget(atom, reason="user asked")
    memory.close()

    assert counts["unknown"] == 1 and counts["live"] == 0
    assert not planted.exists()
    assert receipt["snapshots"]["failed"] == 0


def test_pid_alive_answers_for_an_out_of_range_pid():
    assert pid_alive(10**20) is True
    assert pid_alive(-1) is True


def test_a_malformed_merge_row_does_not_block_the_erase(tmp_path, capsys):
    db, atom, _store_id = _db(tmp_path)
    memory = AgentMemory(db)
    for raw in ("5", "{not json"):
        memory.store.conn.execute(
            "INSERT INTO merges (dropped_id, kept_id, source_ids) VALUES (?, ?, ?)",
            (f"planted-{raw}", "planted-kept", raw))
    memory.store.conn.commit()
    memory.close()

    rc = main(["--state", str(db), "forget", atom, "--yes", "--reason", "user asked"])

    assert rc in (0, 3)
    assert json.loads(capsys.readouterr().out)["counts"]["turns"] == 1


def _link_dir(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        import _winapi
        try:
            _winapi.CreateJunction(str(target), str(link))
            return True
        except OSError:
            return False
    return False


def test_a_linked_snapshot_directory_is_refused(tmp_path, snapshot_root):
    db, _atom, store_id = _db(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = snapshot_dir.store_dir(store_id, db)
    link.parent.mkdir(parents=True, exist_ok=True)
    if not _link_dir(link, elsewhere):
        pytest.skip("this platform cannot create a directory link here")

    with pytest.raises(OSError, match="link"):
        snapshot_dir.create_snapshot_file(db)

    assert list(elsewhere.iterdir()) == []


def test_a_linked_legacy_snapshot_is_never_read(tmp_path):
    import tempfile

    target = tmp_path / "outside.db"
    target.write_bytes(SENTENCE.encode("utf-8"))
    link = Path(tempfile.gettempdir()) / "mneme-replay-1-link.db"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a file symlink here")
    db, atom, _store_id = _db(tmp_path)
    memory = AgentMemory(db)

    receipt = memory.forget(atom, reason="user asked")
    memory.close()

    legacy = next(i for i in receipt["out_of_reach"] if i["class"] == "legacy_temp_snapshots")
    assert legacy["count"] == 0 and legacy["containing_erased_text"] == 0
    assert target.exists()
