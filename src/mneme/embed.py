"""embed.py — a zero-dependency local vector channel for fuzzy retrieval.

BM25 is lexical: it matches whole tokens, so "eat" misses "eating" and a query
misses a morphological variant of the stored fact. The class ships semantic
search, but only with an embedding API (a key, a network hop, a cost). mneme's
floor turns on a vector channel with NEITHER: a character n-gram frequency
vector, hashed to a fixed dimension. Cosine over these catches shared substrings
and morphological variants BM25 alone misses.

HONEST SCOPE (stated, not oversold): this is FUZZY / lexical-similarity matching,
not semantics. It will not connect "car" and "automobile" — only a real
embedding model does that, and mneme takes one as an injected edge
(`AgentMemory(embedder=...)`). What this earns is out-of-the-box hybrid recall
with zero dependencies, and a measurable lift over pure BM25 (see `mneme bench`).
Deterministic: the same text always hashes to the same vector.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

_WORD = re.compile(r"[a-z0-9]+")


def _ngrams(text: str, n: int = 3) -> list[str]:
    grams: list[str] = []
    for word in _WORD.findall(text.lower()):
        padded = f"#{word}#"
        if len(padded) < n:
            grams.append(padded)
            continue
        grams.extend(padded[i:i + n] for i in range(len(padded) - n + 1))
    return grams


class NgramEmbedder:
    """Character-n-gram frequency vector, hashed into `dim` buckets. Zero-dep,
    deterministic, no API. Callable: embed(text) -> vector, so it drops straight
    into recall's embedder slot."""

    def __init__(self, dim: int = 256, n: int = 3):
        self.dim = dim
        self.n = n

    def __call__(self, text: str) -> Sequence[float]:
        vec = [0.0] * self.dim
        for gram in _ngrams(text, self.n):
            h = int(hashlib.sha256(gram.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        return vec


def resolve_embedder(embed):
    """Turn the `embed` argument into an embedder callable:
    None -> no vector channel; "ngram" -> the built-in local NgramEmbedder;
    a callable -> used as-is (a real embedding model edge)."""
    if embed is None or embed is False:
        return None
    if embed == "ngram" or embed is True:
        return NgramEmbedder()
    if callable(embed):
        return embed
    raise ValueError(f"embed must be None, 'ngram', or a callable, got {embed!r}")
