"""Falsifiers for recency-weighted recall.

Load-bearing: (1) recency_weight prefers newer memories among comparably relevant
ones; (2) the recency component is EXPOSED in every hit and the rule is in the
receipt (the weighting is transparent and re-derivable, not opaque); (3) it is
off by default (backward compatible) and deterministic.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory


def _mem_two_prefs():
    # two comparably-relevant facts about the same topic, one older, one newer
    m = AgentMemory(":memory:")
    m.remember("s", [{"id": "old", "role": "user", "text": "I use a Windows laptop."}])
    m.remember("s", [{"id": "new", "role": "user", "text": "I use a Linux laptop."}])
    return m


def test_recency_prefers_the_newer_of_two_comparable_memories():
    m = _mem_two_prefs()
    # without recency: both score the same on "laptop use", id-tiebreak decides
    plain = m.recall("what laptop does the user use", strategy="keyword")
    # with recency: the newer 'Linux laptop' memory ranks first
    weighted = m.recall("what laptop does the user use", strategy="keyword",
                        recency_weight=1.0)
    linux = next(h for h in weighted.hits if "linux" in h.text.lower())
    assert weighted.hits[0].memory_id == linux.memory_id
    assert weighted.hits[0].recency > 0            # recency component is present


def test_recency_component_is_in_the_receipt():
    m = _mem_two_prefs()
    r = m.recall("laptop", strategy="keyword", recency_weight=0.5)
    assert "rrf(recency by ord)" in r.fusion       # the rule is stated
    d = r.as_dict()
    assert all("recency" in h for h in d["hits"])   # every hit exposes it


def test_off_by_default_and_deterministic():
    m = _mem_two_prefs()
    a = m.recall("laptop", strategy="keyword")
    b = m.recall("laptop", strategy="keyword")
    assert a.as_dict() == b.as_dict()
    assert all(h.recency == 0.0 for h in a.hits)   # no recency unless asked
    # weighted is also deterministic
    w1 = m.recall("laptop", strategy="keyword", recency_weight=0.7)
    w2 = m.recall("laptop", strategy="keyword", recency_weight=0.7)
    assert w1.as_dict() == w2.as_dict()


def test_recency_does_not_override_a_clearly_more_relevant_memory():
    m = AgentMemory(":memory:")
    m.remember("s", [{"id": "a", "role": "user", "text": "I am allergic to shellfish."}])
    m.remember("s", [{"id": "b", "role": "user", "text": "I enjoy hiking."}])  # newer, off-topic
    # a modest recency weight should NOT pull the off-topic newer memory above
    # the on-topic older one for an allergy query
    r = m.recall("what is the user allergic to", strategy="keyword", recency_weight=0.3)
    assert "shellfish" in r.hits[0].text.lower()
