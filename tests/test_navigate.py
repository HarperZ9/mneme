"""Navigation recall: the walk, its receipt, its replay, and the bench numbers.

Each behaviour is checked in a pair: the genuine case passes and a one-change
mutation of it fails, so a test that cannot fail does not count.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mneme import AgentMemory  # noqa: E402
from mneme.navbench import control_spread, load_corpus, run_navbench  # noqa: E402
from mneme.navigate import navigate, verify_navigation  # noqa: E402
from mneme.outline import build_outline, deal_to_leaves, leaves, outline_sha256  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "navrecall_corpus.json"


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(FIXTURE)


def test_replay_accepts_genuine_receipt_and_rejects_tampered_hits(corpus):
    rows, _ = corpus
    r = navigate("when does the passport expire", rows)
    assert verify_navigation(r, rows)
    forged = copy.deepcopy(r)
    forged["hits"][0]["memory_id"] = "car01"
    assert not verify_navigation(forged, rows)


def test_replay_rejects_a_flipped_branch_in_the_path(corpus):
    rows, _ = corpus
    r = navigate("what keyboard does the user use", rows)
    assert verify_navigation(r, rows)
    forged = copy.deepcopy(r)
    step = forged["path"][0]["scored"][0]
    step["followed"] = not step["followed"]
    assert not verify_navigation(forged, rows)


def test_replay_rejects_after_a_memory_changes(corpus):
    rows, _ = corpus
    r = navigate("thermostat setting", rows)
    assert verify_navigation(r, rows)
    edited = [dict(x) for x in rows]
    edited[0]["text"] = "User now lives on a boat."
    assert not verify_navigation(r, edited)


def test_receipt_records_unfollowed_branches(corpus):
    rows, _ = corpus
    r = navigate("how old is the cat Miso", rows)
    flags = [s["followed"] for step in r["path"] for s in step["scored"]]
    assert True in flags and False in flags
    assert r["reached"] < r["corpus_size"]


def test_only_reached_memories_are_ranked(corpus):
    rows, _ = corpus
    outline = build_outline([x["text"] for x in rows])
    r = navigate("favourite band", rows, outline=outline)
    reached_leaves = {s["child"] for st in r["path"] for s in st["scored"] if s["followed"]}
    allowed = {rows[i]["id"] for leaf in leaves(outline)
               if leaf.node_id in reached_leaves for i in leaf.members}
    assert {h["memory_id"] for h in r["hits"]} <= allowed
    assert len(allowed) < len(rows)


def test_ratio_one_reaches_no_more_than_ratio_half(corpus):
    rows, queries = corpus
    for q in queries[:10]:
        strict = navigate(q["q"], rows, ratio=1.0)["reached"]
        loose = navigate(q["q"], rows, ratio=0.5)["reached"]
        assert strict <= loose
    assert any(navigate(q["q"], rows, ratio=1.0)["reached"]
               < navigate(q["q"], rows, ratio=0.5)["reached"] for q in queries)


def test_ratio_outside_unit_interval_is_refused(corpus):
    rows, _ = corpus
    with pytest.raises(ValueError):
        navigate("x", rows, ratio=1.5)


def test_outline_is_deterministic_and_content_bound(corpus):
    rows, _ = corpus
    ids, texts = [x["id"] for x in rows], [x["text"] for x in rows]
    a = outline_sha256(build_outline(texts), ids, texts)
    assert a == outline_sha256(build_outline(list(texts)), ids, list(texts))
    texts[5] = texts[5] + " Also enjoys kayaking on lakes."
    assert a != outline_sha256(build_outline(texts), ids, texts)


def test_random_deal_keeps_leaf_sizes_and_members(corpus):
    rows, _ = corpus
    outline = build_outline([x["text"] for x in rows])
    order = list(reversed(range(len(rows))))
    dealt = deal_to_leaves(outline, order)
    assert [len(x.members) for x in leaves(dealt)] == [len(x.members) for x in leaves(outline)]
    assert sorted(i for x in leaves(dealt) for i in x.members) == list(range(len(rows)))
    assert [x.members for x in leaves(dealt)] != [x.members for x in leaves(outline)]


def test_bench_meets_the_prestated_bar_and_control_misses(corpus):
    rows, queries = corpus
    out = run_navbench(rows, queries)
    flat, nav = out["flat"]["recall_at_k"], out["navigation"]["recall_at_k"]
    assert nav >= flat - 0.04                                   # B1
    assert out["navigation"]["mean_reached"] <= 60              # B2
    assert out["control_random_outline"]["recall_at_k"] < flat - 0.04
    assert out == run_navbench(rows, queries)                   # deterministic


def test_control_spread_is_reported_honestly(corpus):
    rows, queries = corpus
    spread = control_spread(rows, queries, range(5))
    assert spread["seeds"] == 5 and len(spread["rates"]) == 5
    assert spread["min"] <= spread["mean_recall_at_k"] <= spread["max"]


def test_agent_memory_navigate_over_a_store():
    mem = AgentMemory(":memory:")
    mem.remember("s1", [{"role": "user", "text": "I am allergic to penicillin."},
                        {"role": "user", "text": "I live in Austin, Texas."}])
    r = mem.navigate("penicillin allergy")
    assert r["schema"] == "mneme.navrecall/1"
    assert any("penicillin" in h["text"].lower() for h in r["hits"])
    assert not any("penicillin" in h["text"].lower()
                   for h in mem.navigate("Austin Texas")["hits"])
