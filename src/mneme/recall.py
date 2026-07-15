"""recall.py — hybrid retrieval (BM25 + optional vector, RRF-fused) with a receipt.

Matches the class leader's retrieval surface (keyword / semantic / hybrid with
Reciprocal Rank Fusion) on a zero-dep floor: BM25 is pure Python over the memory
corpus; a vector channel plugs in through an injected embedder (an optional edge,
never required). The differentiator: every recall returns a RecallReceipt — the
ranked hits with their component scores and the exact fusion rule — so a third
party re-runs the same scorer over the same store and reproduces the ranking.
Nobody else's memory can show its work.

Deterministic: ties break by memory id, so the ranking is stable and re-derivable.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence

from .receipt import Hit, RecallReceipt, content_hash

SCHEMA = "mneme.recall/1"
_TOKEN = re.compile(r"[a-z0-9]+")

Embedder = Callable[[str], Sequence[float]]


def _tok(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class BM25:
    """Okapi BM25 over a fixed corpus. Pure, deterministic; the standard k1/b."""

    def __init__(self, docs: list[list[str]], *, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = docs
        self.N = len(docs)
        self.avgdl = (sum(len(d) for d in docs) / self.N) if self.N else 0.0
        self.df: dict[str, int] = {}
        for d in docs:
            for term in set(d):
                self.df[term] = self.df.get(term, 0) + 1
        self.tf: list[dict[str, int]] = []
        for d in docs:
            counts: dict[str, int] = {}
            for term in d:
                counts[term] = counts.get(term, 0) + 1
            self.tf.append(counts)

    def _idf(self, term: str) -> float:
        n = self.df.get(term, 0)
        return math.log(1 + (self.N - n + 0.5) / (n + 0.5))

    def score(self, query: list[str], i: int) -> float:
        if not self.docs:
            return 0.0
        dl = len(self.docs[i])
        s = 0.0
        for term in query:
            f = self.tf[i].get(term, 0)
            if f == 0:
                continue
            denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
            s += self._idf(term) * (f * (self.k1 + 1)) / denom
        return s


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _rrf(rank: int, k: int = 60) -> float:
    return 1.0 / (k + rank)


def recall(query: str, rows: list, *, strategy: str = "hybrid", top_k: int = 5,
           embedder: Embedder | None = None, rrf_k: int = 60,
           recency_weight: float = 0.0, layer: str | None = None,
           user: str | None = None, session: str | None = None,
           as_of: int | None = None) -> RecallReceipt:
    """Rank `rows` (each a mapping with id/text/layer, optional 'ord') against `query`.

    strategy: 'keyword' (BM25 only), 'vector' (embedder only), 'hybrid' (RRF of
    both — falls back to keyword when no embedder is given, stated in the receipt).
    recency_weight > 0 adds a scale-free recency channel (RRF over `ord`, newest
    first) scaled by the weight, so recent memories are preferred WITHOUT hiding
    it: the recency component rides each hit and the rule is in the receipt, so
    the ranking stays fully re-derivable. Returns a RecallReceipt a third party
    reproduces."""
    ids = [r["id"] for r in rows]
    texts = [r["text"] for r in rows]
    layers = [r["layer"] for r in rows]
    qtok = _tok(query)

    bm = BM25([_tok(t) for t in texts])
    bm_scores = [bm.score(qtok, i) for i in range(len(rows))]

    vec_scores = [0.0] * len(rows)
    have_vec = embedder is not None and strategy in ("vector", "hybrid")
    if have_vec:
        qv = embedder(query)
        vec_scores = [_cosine(qv, embedder(t)) for t in texts]

    if strategy == "keyword" or (strategy == "hybrid" and not have_vec):
        fused = list(bm_scores)
        fusion = "bm25" if strategy == "keyword" else "bm25 (no embedder -> keyword fallback)"
    elif strategy == "vector":
        fused = list(vec_scores)
        # honest label: with no embedder, no cosine ran — say so, and (via the
        # keep rule below) surface no hits rather than arbitrary zero-scored rows.
        fusion = "cosine" if have_vec else "cosine (no embedder -> no ranking)"
    else:
        # RRF over the two rankings (rank position, ties by id for determinism)
        bm_rank = _ranks(bm_scores, ids)
        vec_rank = _ranks(vec_scores, ids)
        fused = [_rrf(bm_rank[i], rrf_k) + _rrf(vec_rank[i], rrf_k) for i in range(len(rows))]
        fusion = f"rrf(k={rrf_k}) over bm25+cosine"

    recency = [0.0] * len(rows)
    if recency_weight > 0 and rows:
        # scale-free recency channel: RRF over `ord` desc (newest = rank 1).
        # relevance ranks are RRF'd too so the two blend on one scale.
        ords = [row.get("ord", 0) for row in rows]
        rec_rank = _ranks(ords, ids)
        rel_rank = _ranks(fused, ids)
        base = [_rrf(rel_rank[i], rrf_k) for i in range(len(rows))]
        recency = [recency_weight * _rrf(rec_rank[i], rrf_k) for i in range(len(rows))]
        fused = [base[i] + recency[i] for i in range(len(rows))]
        fusion = f"{fusion} + {recency_weight}*rrf(recency by ord)"

    order = sorted(range(len(rows)), key=lambda i: (-fused[i], ids[i]))
    # a hit must have actually scored: never surface a zero-scored row as a match
    # just because a channel was requested (a disabled vector channel scored 0).
    keep = lambda i: fused[i] > 0 or recency_weight > 0
    hashes = [r.get("content_sha256", "") for r in rows]
    hits = tuple(
        Hit(memory_id=ids[i], text=texts[i], layer=layers[i],
            bm25=bm_scores[i], vector=vec_scores[i], fused=fused[i],
            recency=recency[i], content_sha256=hashes[i])
        for i in order[:top_k] if keep(i))
    # bind the scorer DEFINITION so two receipts under different scorers are
    # distinguishable: a vector receipt that named 'cosine' but ran a different
    # embedder is no longer indistinguishable from one that ran the built-in.
    embedder_id = getattr(embedder, "name", None) or (
        "callable" if embedder is not None else "none")
    def_sha256 = content_hash(SCHEMA, strategy, fusion, embedder_id,
                              f"bm25(k1={bm.k1},b={bm.b})", f"rrf_k={rrf_k}")
    return RecallReceipt(schema=SCHEMA, query=query, strategy=strategy,
                         fusion=fusion, hits=hits, corpus_size=len(rows),
                         def_sha256=def_sha256, top_k=top_k, layer=layer,
                         user=user, session=session, as_of=as_of,
                         recency_weight=recency_weight)


def _ranks(scores: Sequence[float], ids: Sequence[str]) -> dict[int, int]:
    """1-based rank per index, best score first, ties broken by id (stable)."""
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], ids[i]))
    return {i: pos + 1 for pos, i in enumerate(order)}
