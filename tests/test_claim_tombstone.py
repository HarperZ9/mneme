"""Claim "true forget", the audit side: a tombstone proves the forget, without the content.

After a forget the audit log must show that it happened (one `erase` entry per
erased row, in a chain that verifies and that breaks when an entry is removed),
and must hold none of the content: not the text in any byte form, not a
content-derived id or digest of an erased row, and not a hash a guess of the
text would reproduce. `leaks` is the check, and the planted controls run it
against tombstones that do leak, so the check is shown to be able to fail.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

import claim_scenario
import forensic_util
from mneme import audit_writer
from mneme.receipt import content_hash, memory_hash


def _audit_rows(db) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute("SELECT * FROM audit ORDER BY ord")]
    conn.close()
    return rows


def _guess_hashes(texts: list[str]) -> set[str]:
    """Hashes someone who guessed an erased text could compute without a salt."""
    out = set()
    for text in texts:
        out |= {hashlib.sha256(text.encode()).hexdigest(), content_hash(text),
                content_hash("user", text), memory_hash(text, [], "atomic user fact")}
    return out


def leaks(rows: list[dict], marker: str, ids, texts) -> list[str]:
    """What the audit rows give away: marker forms, erased ids, unsalted hashes."""
    blob = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    found = [f"marker as {name}" for name, data in forensic_util.forms(marker).items()
             if data in blob]
    found += [f"id or digest {value}" for value in ids if value.encode() in blob]
    found += [f"unsalted hash {value}" for value in _guess_hashes(texts)
              if value.encode() in blob]
    return found


def _forgotten(tmp_path, snapshot_root):
    marker = forensic_util.new_marker()
    scene = claim_scenario.build(tmp_path, snapshot_root, marker, export=False)
    memory = scene.open()
    texts = [r["text"] for r in memory.store.memories(include_superseded=True)
             if marker in r["text"]]
    texts += [r["text"] for r in memory.store.turns() if marker in r["text"]]
    before = len(memory.store.audit_log())
    receipt = memory.forget(scene.current_marker_memory(memory), reason="owner asked",
                            allow_collateral=True)
    memory.close()
    return scene, receipt, texts, before


def test_the_audit_trail_proves_the_forget_and_holds_no_content(tmp_path, snapshot_root):
    scene, receipt, texts, before = _forgotten(tmp_path, snapshot_root)
    rows = _audit_rows(scene.db)
    erased = [row for row in rows[before:] if row["op"] == "erase"]
    counts = receipt["counts"]

    # it proves the forget: one entry per erased row, named in the receipt
    assert len(erased) == counts["turns"] + sum(counts["memories"].values()) > 0
    assert {row["memory_id"] for row in erased} == {r["ref"] for r in receipt["erase_refs"]}
    assert all(row["reason"] == "owner asked" for row in erased)
    assert all(len(row["before_sha"]) == 64 and row["after_sha"] == "" for row in erased)
    conn = sqlite3.connect(scene.db)
    assert audit_writer.verify(conn) is True
    # and the proof cannot be quietly removed
    conn.execute("DELETE FROM audit WHERE ord=?", (erased[0]["ord"],))
    assert audit_writer.verify(conn) is False
    conn.rollback()
    conn.close()

    # it holds no content
    assert leaks(rows, scene.marker, scene.ids, texts) == []


def test_two_forgets_of_the_same_text_leave_different_tombstones(tmp_path, snapshot_root):
    """A salted commitment: the same text never gives the same value twice."""
    seen = []
    for name in ("a", "b"):
        folder = tmp_path / name
        folder.mkdir()
        scene = claim_scenario.build(folder, snapshot_root, "Zq-fixed-marker-0123456789abcdef",
                                     export=False)
        memory = scene.open()
        memory.forget(scene.current_marker_memory(memory), allow_collateral=True)
        memory.close()
        seen.append({row["before_sha"] for row in _audit_rows(scene.db)
                     if row["op"] == "erase"})
    assert seen[0] and seen[1] and not seen[0] & seen[1]


# -- planted controls: a tombstone that leaks must be reported -----------------

def test_control_a_tombstone_holding_the_text_is_caught(tmp_path, snapshot_root):
    scene, _receipt, texts, _before = _forgotten(tmp_path, snapshot_root)
    conn = sqlite3.connect(scene.db)
    audit_writer.append(conn, "erase", "er_" + "0" * 24, "L1", "0" * 64, "",
                        f"removed: {texts[0]}")
    conn.commit()
    conn.close()
    assert any("marker as utf-8" in leak for leak in
               leaks(_audit_rows(scene.db), scene.marker, scene.ids, texts))


def test_control_a_tombstone_with_an_unsalted_hash_or_content_id_is_caught(
        tmp_path, snapshot_root):
    scene, _receipt, texts, _before = _forgotten(tmp_path, snapshot_root)
    conn = sqlite3.connect(scene.db)
    audit_writer.append(conn, "erase", scene.ids[0], "L1",
                        hashlib.sha256(texts[0].encode()).hexdigest(), "", "asked")
    conn.commit()
    conn.close()
    found = leaks(_audit_rows(scene.db), scene.marker, scene.ids, texts)
    assert any(leak.startswith("id or digest") for leak in found)
    assert any(leak.startswith("unsalted hash") for leak in found)
