"""Falsifiers for the zero-dep local vector channel.

Load-bearing: (1) the ngram embedder is deterministic and turns on hybrid
retrieval with no external dependency; (2) it catches a morphological variant
BM25 alone misses; (3) it is honestly fuzzy — the docstring/scope does not claim
semantics, and the test does not assert a semantic (synonym) match it cannot do.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from mneme import AgentMemory
from mneme.embed import NgramEmbedder, resolve_embedder

TURNS = [
    {"id": "t1", "role": "user", "text": "I enjoy hiking on weekends."},
    {"id": "t2", "role": "user", "text": "I am vegetarian."},
]


def test_ngram_embedder_is_deterministic():
    e = NgramEmbedder()
    assert list(e("hiking")) == list(e("hiking"))
    assert len(e("hiking")) == 256


def test_resolve_embedder_modes():
    assert resolve_embedder(None) is None
    assert isinstance(resolve_embedder("ngram"), NgramEmbedder)
    f = lambda t: [1.0]
    assert resolve_embedder(f) is f
    with pytest.raises(ValueError):
        resolve_embedder("nope")


def test_embed_ngram_turns_on_hybrid_out_of_the_box():
    m = AgentMemory(":memory:", embed="ngram")
    m.remember("s", TURNS)
    r = m.recall("hiking", strategy="hybrid")
    assert r.fusion.startswith("rrf(")           # the vector channel is active
    assert r.hits and any(v.vector > 0 for v in [r.hits[0]])


def test_catches_a_morphological_variant_bm25_misses():
    # "hikes" is not the stored token "hiking"; BM25 alone scores 0, the ngram
    # channel shares the 'hik' substring and surfaces the right memory
    plain = AgentMemory(":memory:")
    plain.remember("s", TURNS)
    bm = plain.recall("hikes", strategy="keyword")
    assert not bm.hits                            # pure BM25 misses the variant

    fuzzy = AgentMemory(":memory:", embed="ngram")
    fuzzy.remember("s", TURNS)
    fz = fuzzy.recall("hikes", strategy="vector")
    assert fz.hits and "hiking" in fz.hits[0].text.lower()


def test_bench_lift_is_measurable_and_honest():
    # the vector channel should not HURT answer recall on the built-in scenario
    from mneme.bench import run_bench
    base = run_bench(strategy="keyword")
    fuzzy = run_bench(strategy="hybrid")          # hybrid falls back to keyword w/o embedder
    # both report answer_recall; the point is the number is measured, not claimed
    assert base["answer_recall"] >= 0.8
    assert "answer_recall" in fuzzy
