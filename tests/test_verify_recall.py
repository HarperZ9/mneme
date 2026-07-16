"""A recall's receipt promises 're-run the scorer over the same store and get the
identical ranking.' verify_recall SHIPS that check: it re-derives the ranking from the
rows and confirms the receipt, so a fabricated ranking is caught (even with a matching
def hash) and a store that changed no longer reproduces the recall."""

import dataclasses

from mneme.receipt import Hit
from mneme.recall import recall, verify_recall


def _rows():
    return [
        {"id": "m1", "text": "the cat sat on the mat", "layer": "L1", "content_sha256": "a" * 64, "ord": 1},
        {"id": "m2", "text": "a dog ran in the park", "layer": "L1", "content_sha256": "b" * 64, "ord": 2},
        {"id": "m3", "text": "the cat chased the dog", "layer": "L1", "content_sha256": "c" * 64, "ord": 3},
    ]


def test_an_honest_keyword_recall_verifies():
    rows = _rows()
    assert verify_recall(recall("cat", rows, strategy="keyword"), rows) is True


def test_a_forged_ranking_fails_even_with_a_matching_def_hash():
    rows = _rows()
    r = recall("cat", rows, strategy="keyword")
    # prepend a hit the scorer never produced; def_sha256 is untouched (still "matches")
    lie = dataclasses.replace(
        r, hits=(Hit("m2", "a dog ran in the park", "L1", 99.0, 0.0, 99.0),) + r.hits)
    assert verify_recall(lie, rows) is False


def test_a_changed_store_no_longer_reproduces_the_recall():
    rows = _rows()
    r = recall("cat", rows, strategy="keyword")
    changed = [dict(row) for row in rows]
    changed[0]["text"] = "the bird flew away"      # m1 no longer mentions cat
    assert verify_recall(r, changed) is False


def test_verify_accepts_the_on_disk_dict_form():
    rows = _rows()
    assert verify_recall(recall("cat", rows, strategy="keyword").as_dict(), rows) is True


def test_hybrid_recall_reproduces_with_the_same_embedder():
    rows = _rows()

    def emb(t):
        return [float(len(t)), float(t.count("cat"))]   # deterministic toy embedder
    r = recall("cat", rows, strategy="hybrid", embedder=emb)
    assert verify_recall(r, rows, embedder=emb) is True
