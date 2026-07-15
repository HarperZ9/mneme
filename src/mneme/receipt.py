"""receipt.py — content hashing and the two receipts that make memory accountable.

A memory system that cannot show WHY it recalled a fact, or PROVE a fact is
still faithful to its source, is a black box. mneme binds both:

  ProvenanceReceipt — attached to every stored memory: the source it was derived
    from (turn ids), the extractor and criterion that produced it, and a content
    hash over (text + source ids + criterion). The memory row additionally
    snapshots each source's content hash at extraction time, so a source whose
    content later changes no longer matches -> DRIFT (see drift.py).

  RecallReceipt — emitted by every recall: the query, and the ranked hits with
    their component scores (bm25, vector, fused) and the fusion rule. A third
    party re-runs the same scorer over the same store and reproduces the ranking,
    so 'why did you recall this' is answerable and re-checkable, not asserted.

Pure and deterministic: hashes are sha256 over canonical JSON; no clock, no
randomness enters a hash.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


def content_hash(*parts: str) -> str:
    """sha256 over the canonical join of parts (order-significant), 64 hex."""
    h = hashlib.sha256()
    for p in parts:
        h.update(b"\x1f")
        h.update(p.encode("utf-8"))
    return h.hexdigest()


def memory_hash(text: str, source_ids: list[str], criterion: str) -> str:
    """The content hash binding a memory to exactly what it was derived from.
    Any change to the text, the source set, or the extraction criterion changes
    it — that is what a drift check detects."""
    body = json.dumps({"text": text, "sources": sorted(source_ids),
                       "criterion": criterion}, sort_keys=True, ensure_ascii=False)
    return content_hash(body)


@dataclass(frozen=True, slots=True)
class ProvenanceReceipt:
    memory_id: str
    layer: str                 # L0 | L1 | L2 | L3
    source_ids: tuple[str, ...]
    extractor: str
    criterion: str
    content_sha256: str

    def as_dict(self) -> dict:
        return {"memory_id": self.memory_id, "layer": self.layer,
                "source_ids": list(self.source_ids), "extractor": self.extractor,
                "criterion": self.criterion, "content_sha256": self.content_sha256}


@dataclass(frozen=True, slots=True)
class Hit:
    memory_id: str
    text: str
    layer: str
    bm25: float
    vector: float
    fused: float
    recency: float = 0.0        # recency component (0 when recency weighting is off)

    def as_dict(self) -> dict:
        return {"memory_id": self.memory_id, "text": self.text, "layer": self.layer,
                "bm25": round(self.bm25, 6), "vector": round(self.vector, 6),
                "recency": round(self.recency, 6), "fused": round(self.fused, 6)}


@dataclass(frozen=True, slots=True)
class RecallReceipt:
    schema: str
    query: str
    strategy: str              # keyword | vector | hybrid
    fusion: str                # the exact rule (e.g. 'rrf(k=60)')
    hits: tuple[Hit, ...]
    corpus_size: int
    def_sha256: str = field(default="")

    def as_dict(self) -> dict:
        return {"schema": self.schema, "query": self.query, "strategy": self.strategy,
                "fusion": self.fusion, "corpus_size": self.corpus_size,
                "hits": [h.as_dict() for h in self.hits],
                "recheck": "mneme recall --query Q --state DB  (re-run the scorer, reproduce the ranking)"}
