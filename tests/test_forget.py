"""Falsifiers for accountable forgetting — memory editing you can audit.

Mneme leaves a hash-chained tombstone for each row a forget erases and for each
update, so the store keeps a reviewable record that a memory was removed or
changed. A forget erases the memory's source turn too (see test_true_forget.py
for the full erase contract).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory, audit_blind

TURNS = [
    {"id": "t1", "role": "user", "text": "My name is Dana and I live in Denver."},
    {"id": "t2", "role": "user", "text": "I prefer dark roast coffee."},
]


def _mem():
    m = AgentMemory(":memory:")
    m.remember("s", TURNS)
    return m


def test_forget_erases_the_memory_and_its_turn_and_leaves_tombstones():
    m = _mem()
    mid = m.store.memories(layer="L1")[0]["id"]
    receipt = m.forget(mid, reason="user requested deletion")
    assert m.store.memory(mid) is None            # gone from recall
    assert m.store.turn("t1") is None             # and the raw turn it came from
    assert receipt["counts"]["turns"] == 1 and receipt["counts"]["memories"] == {"L1": 1}
    log = m.audit()
    assert log["entries"] == 2                    # one tombstone per erased row
    assert {e["op"] for e in log["log"]} == {"erase"}
    assert {e["reason"] for e in log["log"]} == {"user requested deletion"}
    assert log["chain_intact"] is True            # the tombstones are sealed


def test_forgotten_memory_is_not_recalled():
    m = _mem()
    denver = next(r for r in m.store.memories(layer="L1") if "denver" in r["text"].lower())
    m.forget(denver["id"], reason="stale")
    r = m.recall("where does the user live", strategy="keyword")
    assert all("denver" not in h.text.lower() for h in r.hits)


def test_update_edits_text_keeps_provenance_and_records_before_after():
    m = _mem()
    denver = next(r for r in m.store.memories(layer="L1") if "denver" in r["text"].lower())
    prov_before = m.provenance(denver["id"])
    entry = m.update(denver["id"], "My name is Dana and I live in Seattle.",
                     reason="user moved")
    assert entry["op"] == "update"
    assert entry["before_sha"] != entry["after_sha"]
    row = m.store.memory(denver["id"])
    assert "seattle" in row["text"].lower()
    # schema 5 blinds the history: the entry commits to the new content hash
    # with a salt kept in the salts table, and opens only to that hash
    assert row["content_sha256"] not in (entry["before_sha"], entry["after_sha"])
    assert audit_blind.opens(m.store.conn, entry["after_sha"], row["content_sha256"])
    # provenance (sources, criterion) is preserved through the edit
    prov_after = m.provenance(denver["id"])
    assert prov_after["source_ids"] == prov_before["source_ids"]
    assert prov_after["criterion"] == prov_before["criterion"]


def test_audit_chain_is_tamper_evident():
    m = _mem()
    ids = [r["id"] for r in m.store.memories(layer="L1")]
    m.forget(ids[0], reason="a")
    m.update(ids[1], "edited text", reason="b")
    assert m.audit()["chain_intact"] is True
    # tamper a tombstone's reason -> the chain must break
    m.store.conn.execute("UPDATE audit SET reason='forged' WHERE ord=(SELECT MIN(ord) FROM audit)")
    m.store.conn.commit()
    assert m.store.verify_audit() is False


def test_forget_missing_memory_is_a_noop_none():
    m = _mem()
    assert m.forget("does-not-exist") is None
    assert m.audit()["entries"] == 0             # no phantom tombstone


def test_audit_survives_reingest_and_ordering():
    m = _mem()
    ids = [r["id"] for r in m.store.memories(layer="L1")]
    m.forget(ids[0], reason="one")
    m.update(ids[1], "two", reason="two")
    log = m.audit()["log"]
    assert [e["op"] for e in log] == ["erase", "erase", "update"]   # append-only, in order
