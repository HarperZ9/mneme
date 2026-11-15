"""Claim "true forget": a raw-byte search of every file mneme created.

Each test plants a random marker in a turn, runs the store through every
write path (claim_scenario.py), forgets the memory, closes the store, and
searches every file under every directory mneme can write for the marker in
22 byte forms (forensic_util.py), and for the content-derived ids and digests
of the erased rows. Any hit fails.

The planted controls stay in the suite. Each one breaks the forget in one way
(a row delete with no scrub, a write-ahead log left in place, an export left
on disk, a snapshot left on disk, an audit row that names the content-derived
id) and asserts that the same search reports it. A search that cannot fail
would pass these tests' counterparts and fail here.
"""
from __future__ import annotations

import sqlite3

import pytest

import claim_scenario
import forensic_util
from mneme import erase, erase_receipt


def _forget(scene, **options) -> dict:
    memory = scene.open()
    try:
        return memory.forget(scene.current_marker_memory(memory), reason="owner asked",
                             allow_collateral=True, **options)
    finally:
        memory.close()


def _search(scene) -> list[str]:
    files = forensic_util.files_under(*scene.roots)
    assert scene.db in files, "the database itself must be among the searched files"
    print("forensic search covered:", sorted({p.name for p in files}))
    return (forensic_util.hits(scene.marker, *scene.roots)
            + forensic_util.digest_hits(scene.ids, *scene.roots))


@pytest.mark.parametrize("wal", [False, True], ids=["rollback-journal", "wal"])
def test_after_forget_no_file_mneme_created_holds_the_marker(
        tmp_path, snapshot_root, dead_pid, wal):
    marker = forensic_util.new_marker()
    scene = claim_scenario.build(tmp_path, snapshot_root, marker, wal=wal,
                                 dead_pid=dead_pid)
    before = forensic_util.hits(marker, *scene.roots)
    assert any(h.startswith("mem.db") for h in before), "the marker was never stored"
    assert any(h.startswith("page.html") for h in before), "the export never held it"
    assert any(h.startswith("mneme-replay-") for h in before), "no snapshot held it"

    receipt = _forget(scene)

    assert _search(scene) == []
    assert "scan_hits" not in receipt["findings"]
    assert "rows_present" not in receipt["findings"]


def test_the_search_finds_every_byte_form_it_claims_to(tmp_path):
    """The search itself: each form, planted alone in a file, is reported."""
    marker = forensic_util.new_marker()
    for name, data in forensic_util.forms(marker).items():
        (tmp_path / f"{name.replace('+', '_')}.bin").write_bytes(b"\x00pad" + data + b"pad\xff")
    found = {hit.split(": ", 1)[1] for hit in forensic_util.hits(marker, tmp_path)}
    assert set(forensic_util.forms(marker)) <= found
    split = marker.encode()[:30] + b"\x00\x00\x01\x07" + marker.encode()[30:]
    (tmp_path / "split.bin").write_bytes(split)          # as an overflow page splits it
    assert any(h.startswith("split.bin") for h in forensic_util.hits(marker, tmp_path))
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "other.bin").write_bytes(forensic_util.new_marker().encode())
    assert forensic_util.hits(marker, clean) == []


# -- planted false-success controls: each broken forget must be caught --------

def _delete_rows_only(scene) -> None:
    """A broken forget: delete the memory rows and commit. No source turn, no
    secure_delete, no checkpoint, no VACUUM."""
    conn = sqlite3.connect(scene.db)
    conn.execute("DELETE FROM memories WHERE text LIKE ?", (f"%{scene.marker}%",))
    conn.commit()
    conn.close()


def test_control_a_row_delete_without_scrub_is_caught(tmp_path, snapshot_root):
    scene = claim_scenario.build(tmp_path, snapshot_root, forensic_util.new_marker(),
                                 export=False)
    _delete_rows_only(scene)
    assert any(h.startswith("mem.db: utf-8") for h in _search(scene))


def test_control_a_write_ahead_log_left_behind_is_caught(tmp_path, snapshot_root):
    scene = claim_scenario.build(tmp_path, snapshot_root, forensic_util.new_marker(),
                                 wal=True, export=False)
    reader = sqlite3.connect(scene.db)             # a second connection keeps the WAL
    reader.execute("SELECT count(*) FROM memories").fetchone()
    memory = scene.open()
    memory.store.conn.execute(
        "UPDATE turns SET text = text || ' ' WHERE text LIKE ?", (f"%{scene.marker}%",))
    memory.store.conn.commit()                     # the marker is now in a WAL frame
    conn = memory.store.conn
    conn.execute("PRAGMA secure_delete=ON")
    conn.execute("DELETE FROM memories WHERE text LIKE ?", (f"%{scene.marker}%",))
    conn.execute("DELETE FROM turns WHERE text LIKE ?", (f"%{scene.marker}%",))
    conn.commit()                                  # rows gone; no checkpoint, no VACUUM
    memory.close()
    try:
        assert any(h.startswith("mem.db-wal") for h in _search(scene))
    finally:
        reader.close()


def test_control_an_export_left_on_disk_is_caught(tmp_path, snapshot_root):
    scene = claim_scenario.build(tmp_path, snapshot_root, forensic_util.new_marker())
    receipt = _forget(scene, keep_exports=True)
    assert any(h.startswith("page.html") for h in _search(scene))
    assert receipt["status"] != "erased"
    assert "exports_hold_erased_rows" in receipt["findings"]


def test_control_a_snapshot_left_on_disk_is_caught(tmp_path, snapshot_root, dead_pid,
                                                   monkeypatch):
    scene = claim_scenario.build(tmp_path, snapshot_root, forensic_util.new_marker(),
                                 dead_pid=dead_pid, export=False)
    kept = {"removed": 0, "failed": 0, "removed_while_live": 0}
    monkeypatch.setattr(erase, "remove_snapshots", lambda conn, db: dict(kept))
    monkeypatch.setattr(erase_receipt, "remove_snapshots", lambda conn, db: dict(kept))
    monkeypatch.setattr("mneme.snapshot_dir.startup_sweep", lambda: None)
    _forget(scene)
    assert any(h.startswith("mneme-replay-") for h in _search(scene))


def test_control_an_audit_row_naming_the_content_id_is_caught(tmp_path, snapshot_root):
    """A legacy audit row names the erased memory by its content-derived id."""
    scene = claim_scenario.build(tmp_path, snapshot_root, forensic_util.new_marker(),
                                 export=False)
    memory = scene.open()
    target = scene.current_marker_memory(memory)
    from mneme import audit_writer
    audit_writer.append(memory.store.conn, "update", target, "L1", "b1:" + "0" * 64,
                        "b1:" + "1" * 64, "written by an older mneme")
    memory.store.conn.commit()
    receipt = memory.forget(target, reason="owner asked", allow_collateral=True)
    memory.close()
    assert any(h == f"mem.db: {target}" for h in _search(scene))
    assert receipt["status"] != "erased"
    assert "audit_rows_name_erased_ids" in receipt["findings"]
