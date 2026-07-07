"""Falsifiers for consolidation — the class's signature feature, made auditable.

Load-bearing: (1) near-duplicate memories merge, newest kept, others forgotten
with an audit tombstone; (2) a contradiction (same predicate, different value)
is SURFACED, never silently resolved; (3) applying is auditable and re-checkable.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory


def _mem(texts):
    m = AgentMemory(":memory:")
    m.remember("s", [{"id": f"t{i}", "role": "user", "text": t}
                     for i, t in enumerate(texts)])
    return m


def test_near_duplicates_merge_keeping_the_newest():
    m = _mem([
        "I work in security.",
        "I work in security engineering.",   # near-duplicate of the first
        "I am allergic to peanuts.",
    ])
    before = len(m.store.memories(layer="L1"))
    report = m.consolidate("s")
    after = len(m.store.memories(layer="L1"))
    assert report["merges"], "the two security facts should merge"
    assert report["merged_away"] >= 1
    assert after < before
    # the merge is auditable
    audit = m.audit()
    assert audit["entries"] == report["merged_away"]
    assert audit["chain_intact"] is True
    assert "merged into" in audit["log"][0]["reason"]


def test_contradiction_is_surfaced_not_resolved():
    m = _mem([
        "I live in Denver.",
        "I live in Seattle.",     # contradicts the first (same predicate 'live')
    ])
    report = m.consolidate("s", apply=True)
    assert report["contradictions"], "the two 'live' facts must be flagged"
    conflict = report["contradictions"][0]
    assert conflict["predicate"] == "live"
    assert len(conflict["atoms"]) == 2
    # crucially: BOTH are kept — a contradiction is not auto-resolved
    live_atoms = [r for r in m.store.memories(layer="L1") if "live" in r["text"].lower()]
    assert len(live_atoms) == 2
    assert "not auto-resolved" in conflict["note"]


def test_plan_only_does_not_mutate():
    m = _mem(["I work in security.", "I work in security engineering."])
    before = len(m.store.memories(layer="L1"))
    report = m.consolidate("s", apply=False)
    assert report["applied"] is False
    assert report["merged_away"] == 0
    assert len(m.store.memories(layer="L1")) == before   # nothing forgotten
    assert report["merges"]                              # but the plan is shown


def test_distinct_facts_are_left_alone():
    m = _mem([
        "I live in Denver.",
        "I prefer dark roast coffee.",
        "I work in data science.",
    ])
    report = m.consolidate("s")
    assert report["merges"] == []
    assert report["contradictions"] == []
    assert len(m.store.memories(layer="L1")) == 3


def test_consolidation_is_deterministic():
    texts = ["I work in security.", "I work in security engineering.", "I live in Denver."]
    a = _mem(texts).consolidate("s")
    b = _mem(texts).consolidate("s")
    assert a == b
