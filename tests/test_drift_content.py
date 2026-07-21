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

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory
from mneme.drift import DRIFT, MATCH, UNVERIFIABLE, check_memory


def _mem():
    m = AgentMemory(":memory:")
    m.remember("s", [{"id": "t1", "role": "user", "text": "I live in Denver."}])
    return m


def _two_source_memory():
    m = AgentMemory(":memory:")
    m.store.add_turn("a", "s", "user", "source a")
    m.store.add_turn("b", "s", "user", "source b")
    m.store.add_memory("m-ab", "L1", "derived from a and b", ["a", "b"],
                       "fixture/v1", "two-source fixture", session="s")
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


def test_raw_source_turn_tamper_with_stale_stored_hash_is_drift():
    """A writer bypassing Store.add_turn cannot preserve a false MATCH."""
    m = _mem()
    atom = m.store.memories(layer="L1")[0]["id"]
    m.store.conn.execute(
        "UPDATE turns SET text=? WHERE id=?",
        ("I live in Berlin.", "t1"),
    )
    m.store.conn.commit()

    v = check_memory(m.store, atom)

    assert v.verdict == DRIFT
    assert v.changed_sources == ("t1",)


def test_raw_cited_memory_tamper_with_stale_stored_hash_is_drift():
    """L2/L3 checks re-hash cited memory bytes instead of trusting its row."""
    m = AgentMemory(":memory:")
    m.remember("s", [
        {"id": "t1", "role": "user", "text": "I love hiking the Rockies."},
        {"id": "t2", "role": "user", "text": "I hike the Rockies every weekend."},
    ])
    m.build_scenarios("s")
    scenario = m.store.memories(layer="L2")[0]["id"]
    cited = m.store.memories(layer="L1")[0]["id"]
    m.store.conn.execute(
        "UPDATE memories SET text=? WHERE id=?",
        ("This cited memory was changed without updating its hash.", cited),
    )
    m.store.conn.commit()

    v = check_memory(m.store, scenario)

    assert v.verdict == DRIFT
    assert cited in v.changed_sources


def test_string_shaped_source_ids_cannot_collide_with_list_hash_for_target():
    """JSON ``"ab"`` must not be treated like source list ``["a", "b"]``."""
    m = _two_source_memory()
    m.store.conn.execute(
        "UPDATE memories SET source_ids=? WHERE id=?", ('"ab"', "m-ab"))
    m.store.conn.commit()

    verdict = check_memory(m.store, "m-ab")

    assert verdict.verdict == DRIFT
    assert "provenance" in verdict.reason


def test_string_shaped_source_ids_cannot_collide_for_cited_memory():
    m = _two_source_memory()
    m.store.add_memory("parent", "L2", "parent memory", ["m-ab"],
                       "fixture/v1", "cited-memory fixture", session="s")
    m.store.conn.execute(
        "UPDATE memories SET source_ids=? WHERE id=?", ('"ab"', "m-ab"))
    m.store.conn.commit()

    verdict = check_memory(m.store, "parent")

    assert verdict.verdict == DRIFT
    assert verdict.changed_sources == ("m-ab",)


@pytest.mark.parametrize("bad_ids", ['["a", "a"]', '[""]', '[1]', '{}'])
def test_invalid_source_id_entries_are_drift(bad_ids):
    m = _two_source_memory()
    m.store.conn.execute(
        "UPDATE memories SET source_ids=? WHERE id=?", (bad_ids, "m-ab"))
    m.store.conn.commit()

    verdict = check_memory(m.store, "m-ab")

    assert verdict.verdict == DRIFT
    assert "provenance" in verdict.reason


@pytest.mark.parametrize("source_ids", [["t", "t"], [""], [1]])
def test_store_rejects_source_ids_its_reader_would_reject(source_ids):
    memory = AgentMemory(":memory:")
    memory.store.add_turn("t", "s", "user", "source")

    with pytest.raises(ValueError, match="source_ids"):
        memory.store.add_memory("bad", "L1", "derived", source_ids,
                                "fixture/v1", "fixture", session="s")


@pytest.mark.parametrize("bad_hashes", ['"not-a-map"', '{broken'])
def test_malformed_source_hashes_are_drift_not_an_exception(bad_hashes):
    m = _two_source_memory()
    m.store.conn.execute(
        "UPDATE memories SET source_hashes=? WHERE id=?", (bad_hashes, "m-ab"))
    m.store.conn.commit()

    verdict = check_memory(m.store, "m-ab")

    assert verdict.verdict == DRIFT
    assert "provenance" in verdict.reason


@pytest.mark.parametrize("bad_hashes", [
    '{"a":"short"}',
    '{"a":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}',
    '{"not-cited":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
])
def test_invalid_source_hash_entries_are_drift(bad_hashes):
    m = _two_source_memory()
    m.store.conn.execute(
        "UPDATE memories SET source_hashes=? WHERE id=?", (bad_hashes, "m-ab"))
    m.store.conn.commit()

    verdict = check_memory(m.store, "m-ab")

    assert verdict.verdict == DRIFT
    assert "provenance" in verdict.reason


def test_stale_current_source_hash_wins_over_missing_snapshot():
    m = _mem()
    atom = m.store.memories(layer="L1")[0]["id"]
    m.store.conn.execute(
        "UPDATE memories SET source_hashes='{}' WHERE id=?", (atom,))
    m.store.conn.execute(
        "UPDATE turns SET text=? WHERE id=?", ("tampered source bytes", "t1"))
    m.store.conn.commit()

    verdict = check_memory(m.store, atom)

    assert verdict.verdict == DRIFT
    assert verdict.changed_sources == ("t1",)


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
