"""Falsifiers for the recall receipt — it must bind what recall actually did.

Load-bearing credo repairs:
  1. strategy='vector' with NO embedder must not stamp fusion 'cosine' over a
     ranking that never ran, nor return arbitrary zero-scored rows as hits;
  2. the receipt must record the scope (top_k/layer/user/session/as_of) and a
     runnable recheck command, so a stranger re-runs the SAME slice;
  3. each hit binds the store's content_sha256 of the memory it returned;
  4. def_sha256 names the scorer definition (not a dead field).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory


def _mem():
    m = AgentMemory(":memory:")
    m.remember("s", [{"id": f"t{i}", "role": "user", "text": t} for i, t in enumerate([
        "I live in Denver.", "I prefer tea.", "I am allergic to peanuts.",
        "I work in security.", "I use a Linux laptop."])], user="alice")
    return m


def test_vector_without_embedder_does_not_fake_a_cosine_ranking():
    m = _mem()
    r = m.recall("anything at all", strategy="vector")
    # no cosine ran, so the fusion label must not claim one, and no arbitrary
    # zero-scored rows may be surfaced as hits
    assert "no embedder" in r.fusion
    assert r.hits == ()


def test_receipt_records_scope_and_a_runnable_recheck():
    m = _mem()
    r = m.recall("where does the user live", strategy="keyword", top_k=3,
                 user="alice", session="s")
    d = r.as_dict()
    assert d["scope"]["top_k"] == 3
    assert d["scope"]["user"] == "alice"
    assert d["scope"]["session"] == "s"
    # the recheck command carries the actual scope, not a fixed placeholder
    assert "--user alice" in d["recheck"] and "--session s" in d["recheck"]
    assert "--top-k 3" in d["recheck"]
    assert "Q" not in d["recheck"].split("recall")[1].split("--")[0]  # no literal 'Q' placeholder


def test_each_hit_binds_the_store_content_hash():
    m = _mem()
    hit = m.recall("peanuts", strategy="keyword").hits[0]
    stored = m.store.memory(hit.memory_id)["content_sha256"]
    assert hit.content_sha256 == stored and stored
    assert m.recall("peanuts", strategy="keyword").as_dict()["hits"][0]["content_sha256"] == stored


def test_def_sha256_names_the_scorer_and_distinguishes_embedders():
    plain = _mem().recall("tea", strategy="keyword")
    assert plain.def_sha256 and plain.as_dict()["def_sha256"] == plain.def_sha256
    # a different scorer (ngram vector channel) yields a different definition hash
    fuzzy = AgentMemory(":memory:", embed="ngram")
    fuzzy.remember("s", [{"id": "t0", "role": "user", "text": "I prefer tea."}])
    fz = fuzzy.recall("tea", strategy="vector")
    assert fz.def_sha256 != plain.def_sha256
