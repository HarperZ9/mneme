"""Falsifiers for drift honesty — the verdict must bind source CONTENT, fail
closed, and cover every layer (not just an L1 substring heuristic).

Load-bearing credo repairs:
  1. a source turn edited so the atom text SURVIVES as a substring is still DRIFT
     (content changed, not merely "atom deleted");
  2. an L2 scenario / L3 persona whose cited memory's content changes is DRIFT,
     not a vacuous MATCH;
  3. a memory that cites no sources is UNVERIFIABLE, never MATCH.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory
from mneme.drift import DRIFT, MATCH, UNVERIFIABLE, check_memory


def _mem():
    m = AgentMemory(":memory:")
    m.remember("s", [{"id": "t1", "role": "user", "text": "I live in Denver."}])
    return m


def test_source_append_that_preserves_the_atom_substring_is_drift():
    # the substring heuristic said MATCH here: the atom "I live in Denver." is
    # still contained in the appended turn. Binding source CONTENT catches it.
    m = _mem()
    assert m.drift()["overall"] == MATCH
    atom = m.store.memories(layer="L1")[0]["id"]
    m.store.add_turn("t1", "s", "user",
                     "I live in Denver. Actually that was a lie, I moved to Berlin.")
    v = check_memory(m.store, atom)
    assert v.verdict == DRIFT
    assert "t1" in v.changed_sources


def test_l2_scenario_drifts_when_a_cited_atom_content_changes():
    m = AgentMemory(":memory:")
    m.remember("s", [{"id": "t1", "role": "user", "text": "I love hiking the Rockies."},
                     {"id": "t2", "role": "user", "text": "I hike the Rockies every weekend."}])
    m.build_scenarios("s")
    scenario = m.store.memories(layer="L2")[0]["id"]
    assert check_memory(m.store, scenario).verdict == MATCH   # fresh
    # edit a cited atom's content through the audited update path
    atom = m.store.memories(layer="L1")[0]["id"]
    m.update(atom, "I completely changed my mind about hiking.", reason="changed")
    v = check_memory(m.store, scenario)
    assert v.verdict == DRIFT
    assert atom in v.changed_sources


def test_l3_persona_drifts_when_a_cited_atom_content_changes():
    m = _mem()
    m.remember("s", [{"id": "t2", "role": "user", "text": "I prefer tea."}])
    m.persona("s")
    persona = m.store.memories(layer="L3")[0]["id"]
    assert check_memory(m.store, persona).verdict == MATCH
    atom = m.store.memories(layer="L1")[0]["id"]
    m.update(atom, "I now live somewhere else entirely.", reason="moved")
    assert check_memory(m.store, persona).verdict == DRIFT


def test_memory_with_no_sources_is_unverifiable_not_match():
    m = AgentMemory(":memory:")
    m.store.add_memory("m1", "L1", "the operator approved the deploy", [],
                       "custom/v1", "atomic user fact")
    v = check_memory(m.store, "m1")
    assert v.verdict == UNVERIFIABLE
    assert v.verdict != MATCH
