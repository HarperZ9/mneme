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


class ProvenanceFormatError(ValueError):
    """Stored provenance is not the strict JSON shape Mneme hashes."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProvenanceFormatError(
                f"malformed provenance: duplicate JSON key {key!r}")
        result[key] = value
    return result


def _provenance_json(raw: object, field: str) -> object:
    if not isinstance(raw, str):
        raise ProvenanceFormatError(
            f"malformed provenance: {field} must be encoded JSON text")
    try:
        return json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ProvenanceFormatError(
            f"malformed provenance: {field} is not valid JSON") from exc


def decode_provenance(source_ids_json: object,
                      source_hashes_json: object) -> tuple[list[str], dict[str, str]]:
    """Decode the single accepted stored-provenance representation.

    Empty source lists remain valid because Mneme represents them as
    UNVERIFIABLE. Source identifiers themselves must be non-empty and unique.
    Snapshot values are lowercase SHA-256 strings; snapshots may omit a cited
    source when it was unavailable at extraction, but may not name another id.
    """
    source_ids = validate_source_ids(
        _provenance_json(source_ids_json, "source_ids"))
    source_hashes = _provenance_json(source_hashes_json, "source_hashes")
    if not isinstance(source_hashes, dict):
        raise ProvenanceFormatError(
            "malformed provenance: source_hashes must be an object")
    for source_id, digest in source_hashes.items():
        if (not isinstance(source_id, str) or not source_id
                or not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise ProvenanceFormatError(
                "malformed provenance: source_hashes must map non-empty source ids "
                "to lowercase SHA-256 strings")
        if source_id not in source_ids:
            raise ProvenanceFormatError(
                f"malformed provenance: snapshot {source_id!r} is not a cited source")
    return source_ids, dict(source_hashes)


def validate_source_ids(source_ids: object) -> list[str]:
    """Validate the in-memory form used before hashing or JSON storage."""
    if not isinstance(source_ids, list) or any(
            not isinstance(source_id, str) or not source_id for source_id in source_ids):
        raise ProvenanceFormatError(
            "malformed provenance: source_ids must be a list of non-empty strings")
    if len(set(source_ids)) != len(source_ids):
        raise ProvenanceFormatError(
            "malformed provenance: source_ids must not contain duplicates")
    return list(source_ids)


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
    content_sha256: str = ""    # the store's hash of the memory this hit returned

    def as_dict(self) -> dict:
        return {"memory_id": self.memory_id, "text": self.text, "layer": self.layer,
                "content_sha256": self.content_sha256,
                "bm25": round(self.bm25, 6), "vector": round(self.vector, 6),
                "recency": round(self.recency, 6), "fused": round(self.fused, 6)}


def _flag(value: str) -> str:
    """Minimal shell-safe rendering of a value for the recheck command."""
    return f'"{value}"' if (value == "" or any(c.isspace() for c in value)) else value


@dataclass(frozen=True, slots=True)
class RecallReceipt:
    schema: str
    query: str
    strategy: str              # keyword | vector | hybrid
    fusion: str                # the exact rule (e.g. 'rrf(k=60)')
    hits: tuple[Hit, ...]
    corpus_size: int
    def_sha256: str = field(default="")   # hash of the scorer DEFINITION (see recall)
    top_k: int = 5
    layer: str | None = None
    user: str | None = None
    session: str | None = None
    as_of: int | None = None
    recency_weight: float = 0.0

    def _recheck(self) -> str:
        parts = ["mneme recall", _flag(self.query), "--strategy", self.strategy,
                 "--top-k", str(self.top_k)]
        if self.layer:
            parts += ["--layer", self.layer]
        if self.user is not None:
            parts += ["--user", _flag(self.user)]
        if self.session is not None:
            parts += ["--session", _flag(self.session)]
        if self.as_of is not None:
            parts += ["--as-of", str(self.as_of)]
        if self.recency_weight:
            parts += ["--recency", str(self.recency_weight)]
        return " ".join(parts) + "  (re-run over the same store, reproduce the ranking)"

    def as_dict(self) -> dict:
        return {"schema": self.schema, "query": self.query, "strategy": self.strategy,
                "fusion": self.fusion, "def_sha256": self.def_sha256,
                "corpus_size": self.corpus_size,
                "scope": {"top_k": self.top_k, "layer": self.layer, "user": self.user,
                          "session": self.session, "as_of": self.as_of,
                          "recency_weight": self.recency_weight},
                "hits": [h.as_dict() for h in self.hits],
                "recheck": self._recheck()}
