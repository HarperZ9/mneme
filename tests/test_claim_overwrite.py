"""Claim "true forget": the files an erase deletes are overwritten first, and
the receipt says `erased` only when nothing known is left.

A plain unlink leaves a file's bytes in freed disk blocks. These tests watch
the three files an erase removes at the moment before they go: the rollback
journal of the erase transaction, a replay snapshot, and a registered export.
Each must hold the marker before the overwrite (so the hazard is real) and
only zeros when it is unlinked.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

import claim_scenario
import forensic_util
from mneme import AgentMemory, cli, erase_exports, erase_journal, wipe


def _forget(scene, **options) -> dict:
    memory = scene.open()
    try:
        return memory.forget(scene.current_marker_memory(memory), reason="owner asked",
                             allow_collateral=True, **options)
    finally:
        memory.close()


def _watch_unlinks(monkeypatch) -> dict[str, bytes]:
    """Record each file's bytes at the moment mneme unlinks it."""
    seen: dict[str, bytes] = {}
    real = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.is_file():
            seen[self.name] = self.read_bytes()
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    return seen


def test_the_rollback_journal_of_an_erase_is_overwritten_before_it_goes(
        tmp_path, snapshot_root, monkeypatch):
    marker = forensic_util.new_marker()
    scene = claim_scenario.build(tmp_path, snapshot_root, marker, export=False)
    journal = Path(f"{scene.db}-journal")
    seen = {}
    real = erase_journal._overwrite

    def overwrite(path):
        seen["before"] = Path(path).read_bytes()
        written = real(path)
        seen["after"] = Path(path).read_bytes()
        return written

    monkeypatch.setattr(erase_journal, "_overwrite", overwrite)
    receipt = _forget(scene)

    assert marker.encode() in seen["before"], "the journal never held the erased text"
    assert seen["after"] and set(seen["after"]) == {0}
    assert len(seen["after"]) == len(seen["before"])
    assert receipt["structural"]["journal_file"] == "overwritten"
    assert not journal.exists()
    conn = sqlite3.connect(scene.db)                 # the modes are back, the lock is free
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    conn.execute("CREATE TABLE probe(x)")
    conn.close()


def test_another_connection_can_write_again_once_the_erase_returns(tmp_path, snapshot_root):
    scene = claim_scenario.build(tmp_path, snapshot_root, forensic_util.new_marker(),
                                 export=False)
    memory = scene.open()
    other = sqlite3.connect(scene.db, timeout=0.5)
    memory.forget(scene.current_marker_memory(memory), allow_collateral=True)
    other.execute("INSERT INTO meta(key, value) VALUES('probe', '1')")
    other.commit()
    assert memory.store.conn.execute("PRAGMA locking_mode").fetchone()[0] == "normal"
    assert memory.audit()["chain_intact"] is True
    other.close()
    memory.close()


def test_a_refused_erase_puts_the_journal_and_locking_modes_back(tmp_path, snapshot_root):
    scene = claim_scenario.build(tmp_path, snapshot_root, forensic_util.new_marker(),
                                 export=False)
    memory = scene.open()
    target = scene.current_marker_memory(memory)
    from mneme.erase import Selection, StalePlanError, apply_erase
    with pytest.raises(StalePlanError):
        apply_erase(memory.store, Selection(memories=(target,)), "0" * 64)
    conn = memory.store.conn
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert conn.execute("PRAGMA locking_mode").fetchone()[0] == "normal"
    other = sqlite3.connect(scene.db, timeout=0.5)
    other.execute("INSERT INTO meta(key, value) VALUES('probe', '1')")
    other.commit()
    other.close()
    assert memory.store.memory(target) is not None
    memory.close()


def test_a_journal_that_cannot_be_overwritten_is_reported(tmp_path, snapshot_root,
                                                          monkeypatch):
    scene = claim_scenario.build(tmp_path, snapshot_root, forensic_util.new_marker(),
                                 export=False)

    def refuse(path):
        raise PermissionError("held by another program")

    monkeypatch.setattr(erase_journal, "_overwrite", refuse)
    memory = scene.open()
    receipt = memory.forget(scene.current_marker_memory(memory), allow_collateral=True)
    assert receipt["structural"]["journal_file"] == "overwrite_failed"
    assert "could not be overwritten" in receipt["residue"]["freed_clusters"]["note"]
    conn = memory.store.conn
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert conn.execute("PRAGMA locking_mode").fetchone()[0] == "normal"
    memory.close()


def test_an_erase_refused_by_a_locked_database_changes_no_mode(tmp_path, snapshot_root):
    scene = claim_scenario.build(tmp_path, snapshot_root, forensic_util.new_marker(),
                                 export=False)
    memory = scene.open()
    target = scene.current_marker_memory(memory)
    conn = memory.store.conn
    conn.execute("PRAGMA busy_timeout=100")
    writer = sqlite3.connect(scene.db)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO meta(key, value) VALUES('probe', '1')")
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        memory.forget(target, allow_collateral=True)
    assert memory.store.memory(target) is not None
    writer.commit()
    writer.close()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert conn.execute("PRAGMA locking_mode").fetchone()[0] == "normal"
    receipt = memory.forget(target, allow_collateral=True)
    assert receipt["structural"]["journal_file"] == "overwritten"
    assert memory.store.memory(target) is None
    memory.close()


def test_a_wal_store_says_its_log_was_not_overwritten(tmp_path, snapshot_root):
    scene = claim_scenario.build(tmp_path, snapshot_root, forensic_util.new_marker(),
                                 wal=True, export=False)
    receipt = _forget(scene)
    assert receipt["structural"]["journal_file"] == "not_overwritten_wal"
    assert "not overwritten" in receipt["residue"]["freed_clusters"]["note"]


def test_a_replay_snapshot_is_zeroed_before_it_is_unlinked(
        tmp_path, snapshot_root, dead_pid, monkeypatch):
    marker = forensic_util.new_marker()
    scene = claim_scenario.build(tmp_path, snapshot_root, marker, export=False)
    left = claim_scenario.leave_dead_snapshot(scene, dead_pid)
    assert marker.encode() in left.read_bytes()
    seen = _watch_unlinks(monkeypatch)
    _forget(scene)
    assert not left.exists()
    assert seen[left.name] and set(seen[left.name]) == {0}


def test_overwrite_refuses_a_link_and_reports_a_missing_file(tmp_path, monkeypatch):
    target = tmp_path / "data.bin"
    target.write_bytes(b"secret" * 100)
    assert wipe.overwrite(target) == 600
    assert target.read_bytes() == bytes(600)
    with pytest.raises(FileNotFoundError):
        wipe.overwrite_and_unlink(tmp_path / "absent.bin")
    with pytest.raises(OSError):
        wipe.overwrite(tmp_path)                      # a directory is not a regular file
    target.write_bytes(b"keep me")
    link = tmp_path / "link.bin"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        # this account may not create symlinks (Windows without the privilege):
        # stand in a path the link check reports as a link, so the refusal runs
        link = target
        monkeypatch.setattr(wipe, "_is_link", lambda path: True)
    with pytest.raises(OSError, match="is a link"):
        wipe.overwrite(link)
    assert target.read_bytes() == b"keep me"


# -- exports ---------------------------------------------------------------------

def test_an_export_mneme_wrote_is_registered_and_removed_by_the_forget(
        tmp_path, snapshot_root, monkeypatch):
    marker = forensic_util.new_marker()
    scene = claim_scenario.build(tmp_path, snapshot_root, marker)
    page = scene.exports / "page.html"
    assert marker.encode() in page.read_bytes()
    conn = sqlite3.connect(scene.db)
    from mneme import snapshot_dir
    store_id = snapshot_dir.store_id_of(conn)
    conn.close()
    listed = erase_exports.entries(store_id, scene.db)
    assert [Path(e["path"]) for e in listed] == [page.resolve()]
    seen = _watch_unlinks(monkeypatch)

    receipt = _forget(scene)

    assert not page.exists()
    assert set(seen["page.html"]) == {0}
    assert receipt["exports"] == {"registered": 1, "removed": 1, "holding_erased_rows": 0,
                                  "unchecked": 0, "missing": 0}
    assert erase_exports.entries(store_id, scene.db) == []
    assert "exports_hold_erased_rows" not in receipt["findings"]


def test_an_export_changed_since_mneme_wrote_it_is_left_and_reported(
        tmp_path, snapshot_root):
    marker = forensic_util.new_marker()
    scene = claim_scenario.build(tmp_path, snapshot_root, marker)
    page = scene.exports / "page.html"
    page.write_bytes(page.read_bytes() + b"<!-- my notes -->")

    receipt = _forget(scene)

    assert page.exists(), "a file the owner changed is not mneme's to delete"
    assert "exports_hold_erased_rows" in receipt["findings"]
    assert receipt["status"] == "erased_copies_remain"
    assert receipt["exports"]["holding_erased_rows"] == 1


def test_an_export_without_the_erased_row_stays(tmp_path, snapshot_root):
    scene = claim_scenario.build(tmp_path, snapshot_root, forensic_util.new_marker(),
                                 export=False)
    memory = scene.open()
    memory.forget(scene.current_marker_memory(memory), allow_collateral=True)
    memory.close()
    page = scene.exports / "later.html"
    assert cli.main(["--state", str(scene.db), "inspect", "--out", str(page)]) == 0
    memory = AgentMemory(scene.db)
    desk = next(r["id"] for r in memory.store.memories() if "oolong" in r["text"])
    receipt = memory.forget(desk, allow_collateral=True)
    memory.close()
    assert not page.exists()                          # it listed the oolong row
    assert receipt["exports"]["removed"] == 1
    assert cli.main(["--state", str(scene.db), "inspect", "--out", str(page)]) == 0
    memory = AgentMemory(scene.db)
    memory.remember("new", [{"role": "user", "text": "I enjoy long walks at dusk."}])
    walk = next(r["id"] for r in memory.store.memories() if "dusk" in r["text"])
    receipt = memory.forget(walk, allow_collateral=True)
    memory.close()
    assert page.exists()                              # written before that row existed
    assert receipt["exports"] == {"registered": 1, "removed": 0, "holding_erased_rows": 0,
                                  "unchecked": 0, "missing": 0}


# -- the status is attainable ------------------------------------------------------

def test_a_forget_in_a_store_with_a_persona_and_scenarios_can_say_erased(tmp_path):
    """Scenario and persona rows repeat the atoms they cite. When one atom is
    erased, the kept atoms' lines in those rows are not residue."""
    memory = AgentMemory(tmp_path / "mem.db")
    memory.remember("home", [
        {"role": "user", "text": "I live in Denver near the old rail yard."},
        {"role": "user", "text": "I prefer green tea in the early morning."},
        {"role": "user", "text": "I always walk the dog before breakfast."}], user="alice")
    memory.build_scenarios("home", user="alice", min_shared=0)
    memory.persona("home", user="alice")
    target = next(r["id"] for r in memory.store.memories() if "Denver" in r["text"])

    receipt = memory.forget(target, reason="owner asked", allow_collateral=True)

    assert receipt["findings"] == []
    assert receipt["status"] == "erased"
    assert len(memory.store.memories(layer="L1")) == 2
    memory.close()
