"""Falsifiers for mneme — the accountable agent-memory database.

Load-bearing (the differentiators no competitor ships):
  1. recall returns a receipt that REPRODUCES the ranking (re-run -> identical).
  2. a memory whose source turn CHANGES is flagged DRIFT (not silently kept).
  3. every atom carries provenance back to its source turn.
Plus the table-stakes: 4-tier storage, deterministic rebuild, hybrid retrieval,
idempotent ingest, persona grounded in its atoms.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from mneme import AgentMemory
from mneme.drift import DRIFT, MATCH, UNVERIFIABLE

SESSION = [
    {"id": "t1", "role": "user", "text": "My name is Dana and I live in Denver."},
    {"id": "t2", "role": "user", "text": "I prefer dark roast coffee and I work in security."},
    {"id": "t3", "role": "assistant", "text": "Nice to meet you, Dana."},
    {"id": "t4", "role": "user", "text": "I am allergic to peanuts."},
]


def _mem():
    m = AgentMemory(":memory:")
    m.remember("s1", SESSION)
    return m


def test_extracts_atoms_only_from_user_turns_with_provenance():
    m = _mem()
    atoms = m.store.memories(layer="L1")
    assert len(atoms) == 3                       # 3 user fact-turns; the assistant turn is not memory
    for a in atoms:
        prov = m.provenance(a["id"])
        assert prov["layer"] == "L1"
        assert prov["source_ids"][0] in {"t1", "t2", "t4"}
        assert prov["content_sha256"]


def test_recall_receipt_reproduces_the_ranking():
    m = _mem()
    r1 = m.recall("where does the user live", strategy="keyword")
    r2 = m.recall("where does the user live", strategy="keyword")
    assert [h.memory_id for h in r1.hits] == [h.memory_id for h in r2.hits]
    assert r1.as_dict() == r2.as_dict()          # the whole receipt is deterministic
    top = r1.hits[0]
    assert "denver" in top.text.lower()          # the right memory surfaced
    assert top.bm25 > 0 and top.fused > 0        # scored, not guessed


def test_recall_receipt_carries_scores_and_fusion_rule():
    m = _mem()
    r = m.recall("coffee preference", strategy="keyword")
    d = r.as_dict()
    assert d["schema"] == "mneme.recall/1"
    assert d["fusion"].startswith("bm25")
    assert d["hits"][0]["text"].lower().find("coffee") >= 0
    assert "recheck" in d                        # the receipt tells you how to re-derive it


def test_stale_memory_flags_itself_drift():
    m = _mem()
    assert m.drift()["overall"] == MATCH         # fresh: every atom grounded and unchanged
    # the user's source turn changes under the atom
    m.store.add_turn("t1", "s1", "user", "My name is Dana and I live in Seattle now.")
    report = m.drift()
    assert report["overall"] == DRIFT
    # the atom about Denver no longer matches its source turn
    drifted_texts = [next(v for v in report["verdicts"] if v["memory_id"] == mid)
                     for mid in report["drifted"]]
    assert any("t1" in v["changed_sources"] for v in drifted_texts)


def test_missing_source_is_unverifiable_not_match():
    m = _mem()
    # delete a source turn out from under its atom
    m.store.conn.execute("DELETE FROM turns WHERE id='t4'")
    m.store.conn.commit()
    report = m.drift()
    assert report["overall"] in (DRIFT, UNVERIFIABLE)
    assert report["unverifiable"], "an atom whose source is gone must be UNVERIFIABLE"


def test_ingest_is_idempotent():
    m = _mem()
    before = len(m.store.memories(layer="L1"))
    m.remember("s1", SESSION)                     # same turns again
    after = len(m.store.memories(layer="L1"))
    assert before == after == 3                   # content ids collapse duplicates


def test_deterministic_rebuild_is_byte_identical():
    a = AgentMemory(":memory:"); a.remember("s", SESSION)
    b = AgentMemory(":memory:"); b.remember("s", SESSION)
    ax = [(r["id"], r["content_sha256"]) for r in a.store.memories(layer="L1")]
    bx = [(r["id"], r["content_sha256"]) for r in b.store.memories(layer="L1")]
    assert ax == bx


def test_hybrid_falls_back_to_keyword_without_embedder_honestly():
    m = _mem()
    r = m.recall("security work", strategy="hybrid")
    assert "keyword fallback" in r.fusion        # stated, not silently pretended to be vector


def test_hybrid_rrf_fuses_bm25_and_vector_when_embedder_present():
    # a toy deterministic embedder: bag-of-chars vector, proves the vector channel
    def embed(text: str):
        v = [0.0] * 26
        for c in text.lower():
            if "a" <= c <= "z":
                v[ord(c) - 97] += 1.0
        return v

    m = AgentMemory(":memory:", embedder=embed)
    m.remember("s", SESSION)
    r = m.recall("peanuts allergy", strategy="hybrid")
    assert r.fusion.startswith("rrf(")
    assert r.hits and any(h.vector > 0 for h in r.hits)


def test_persona_is_grounded_in_its_atoms():
    m = _mem()
    p = m.persona("s1")
    assert p["facts"] == 3
    assert len(p["grounded_in"]) == 3
    # the persona is stored as L3 citing the atoms -> itself drift-checkable
    l3 = m.store.memories(layer="L3")
    assert len(l3) == 1
    prov = m.provenance(l3[0]["id"])
    assert set(prov["source_ids"]) == set(p["grounded_in"])
