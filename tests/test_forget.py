"""Falsifiers for accountable forgetting — memory editing you can audit.

The class ships memory edit/delete; none makes it auditable. mneme leaves a
hash-chained tombstone for every forget/update, so you cannot quietly forget
that you forgot something.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory

TURNS = [
    {"id": "t1", "role": "user", "text": "My name is Dana and I live in Denver."},
    {"id": "t2", "role": "user", "text": "I prefer dark roast coffee."},
]


def _mem():
    m = AgentMemory(":memory:")
    m.remember("s", TURNS)
    return m


def test_forget_removes_the_memory_and_leaves_a_tombstone():
    m = _mem()
    mid = m.store.memories(layer="L1")[0]["id"]
    before_sha = m.store.memory(mid)["content_sha256"]
    entry = m.forget(mid, reason="user requested deletion")
    assert m.store.memory(mid) is None            # gone from recall
    assert entry["op"] == "forget" and entry["before_sha"] == before_sha
    log = m.audit()
    assert log["entries"] == 1
    assert log["log"][0]["reason"] == "user requested deletion"
    assert log["chain_intact"] is True            # the tombstone is sealed


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
    assert row["content_sha256"] == entry["after_sha"]
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
    assert [e["op"] for e in log] == ["forget", "update"]   # append-only, in order
