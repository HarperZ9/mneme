"""Falsifiers for temporal memory — a fact's timeline, backed by the audit log.

Load-bearing: (1) superseding a fact KEEPS the old value with a validity window,
so history shows the timeline and recall(as_of) reconstructs the past; (2)
current recall only sees the current fact; (3) FORGET (GDPR) erases — a forgotten
fact never appears in history, unlike a superseded one; (4) every transition is
in the audit log.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory


def _lived():
    m = AgentMemory(":memory:")
    m.remember("s", [{"id": "t1", "role": "user", "text": "I live in Denver."}], user="a")
    denver = m.store.memories(layer="L1", user="a")[0]["id"]
    m.supersede(denver, "I live in Seattle.", reason="user moved")
    return m, denver


def test_supersede_keeps_the_old_fact_for_history():
    m, denver = _lived()
    hist = m.history(predicate="lives_in", user="a")
    texts = [t["text"] for t in hist["timeline"]]
    assert "I live in Denver." in texts and "I live in Seattle." in texts
    assert hist["transitions"] == 1
    assert hist["current"] == "I live in Seattle."
    # the old fact carries its validity window and what replaced it
    old = next(t for t in hist["timeline"] if "Denver" in t["text"])
    assert old["current"] is False and old["until_ord"] is not None
    assert old["superseded_by"]


def test_current_recall_only_sees_the_current_fact():
    m, _ = _lived()
    r = m.recall("where does the user live", strategy="keyword", user="a")
    assert r.hits and "seattle" in r.hits[0].text.lower()
    assert all("denver" not in h.text.lower() for h in r.hits)   # the past is not current


def test_point_in_time_recall_reconstructs_the_past():
    m, denver = _lived()
    # the ordinal at which Denver was still valid: its created_ord
    denver_ord = m.store.memory(denver)["created_ord"]
    past = m.recall("where does the user live", strategy="keyword", user="a",
                    as_of=denver_ord)
    assert past.hits and "denver" in past.hits[0].text.lower()   # as of then, Denver


def test_forget_erases_but_supersede_preserves():
    m, denver = _lived()
    # Denver was superseded -> still in history
    assert any("Denver" in t["text"] for t in m.history(predicate="lives_in", user="a")["timeline"])
    # now FORGET the (superseded) Denver memory -> GDPR erasure removes it
    m.forget(denver, reason="right to be forgotten")
    hist = m.history(predicate="lives_in", user="a")
    assert all("Denver" not in t["text"] for t in hist["timeline"])   # erased, not resurrectable


def test_every_transition_is_in_the_audit_log():
    m, _ = _lived()
    audit = m.audit()
    assert audit["chain_intact"] is True
    assert any(e["op"] == "supersede" for e in audit["log"])


def test_supersede_missing_or_closed_is_none():
    m, denver = _lived()
    assert m.supersede("nonexistent", "x") is None
    assert m.supersede(denver, "third city") is None   # already superseded
