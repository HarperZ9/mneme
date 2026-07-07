"""Falsifiers for the grounded entity graph (mneme's graph memory).

Load-bearing: (1) typed relations (lives_in, allergic_to, ...) are extracted
with the right object; (2) every edge cites its source atom, so the graph is
drift-checkable — forgetting an atom removes the edges it supported; (3)
deterministic and honestly scoped (cue-based, not general NER).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory
from mneme.entity import named_entities, relations_in


def test_typed_relations_extracted():
    rels = relations_in("I live in Denver and I am allergic to peanuts.")
    by_pred = {r["predicate"]: r["object"] for r in rels}
    assert by_pred["lives_in"] == "denver"
    assert by_pred["allergic_to"] == "peanuts"


def test_named_entities_skip_sentence_initial_word():
    ents = named_entities("Denver is home. My friend Alice visited the Rockies.")
    # 'Denver' begins its sentence (skipped as a proper-noun heuristic); Alice
    # and Rockies are mid-sentence proper nouns
    assert "Alice" in ents and "Rockies" in ents


def test_graph_edges_are_grounded_and_drift_checkable():
    m = AgentMemory(":memory:")
    m.remember("s", [
        {"id": "t1", "role": "user", "text": "I live in Denver."},
        {"id": "t2", "role": "user", "text": "I am allergic to shellfish."},
    ], user="alice")
    g = m.entity_graph(user="alice")
    preds = {e["predicate"] for e in g["edges"]}
    assert "lives_in" in preds and "allergic_to" in preds
    assert g["grounded"] is True
    assert all(e["source"] for e in g["edges"])       # every edge cites an atom
    # forget the atom -> the edges it supported are no longer grounded
    denver_edge = next(e for e in g["edges"] if e["predicate"] == "lives_in")
    m.forget(denver_edge["source"], reason="test")
    g2 = m.entity_graph(user="alice")
    assert "lives_in" not in {e["predicate"] for e in g2["edges"]}


def test_graph_is_user_scoped():
    m = AgentMemory(":memory:")
    m.remember("s", [{"role": "user", "text": "I live in Denver."}], user="alice")
    m.remember("s", [{"role": "user", "text": "I live in Boston."}], user="bob")
    alice = m.entity_graph(user="alice")
    objs = {e["to"] for e in alice["edges"]}
    assert "denver" in objs and "boston" not in objs


def test_graph_is_deterministic():
    m = AgentMemory(":memory:")
    m.remember("s", [{"role": "user", "text": "I work in security and I use Linux."}])
    assert m.entity_graph() == m.entity_graph()
