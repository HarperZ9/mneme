"""drift.py — memory that flags its own staleness.

Every other agent-memory system silently keeps a fact after its source changed;
you find out when the agent acts on a stale memory. mneme snapshots each source's
content hash at extraction time and, on every check, re-reads the source's CURRENT
content hash and compares. This works for every layer: an L1 atom's source is a
turn; an L2 scenario's and L3 persona's sources are the memories they cite. The
verdict per memory:

  MATCH        every source is present and its content is unchanged
  DRIFT        a source's content changed since extraction (or the memory row was
               altered so its own hash no longer reproduces)
  UNVERIFIABLE a source is gone, the memory cites no source, or a source's
               extraction-time hash was never recorded (grounding unconfirmable)

Fail closed: absence of a verifiable source is UNVERIFIABLE, never a vacuous
MATCH. This is the freshness verdict none of the class carries. Pure: it reads
the store and re-hashes; a consumer re-runs it and gets the same verdict.
"""
from __future__ import annotations

from dataclasses import dataclass

from .receipt import (ProvenanceFormatError, content_hash, decode_provenance,
                      memory_hash)

MATCH = "MATCH"
DRIFT = "DRIFT"
UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True, slots=True)
class MemoryVerdict:
    memory_id: str
    verdict: str
    reason: str
    changed_sources: tuple[str, ...]
    missing_sources: tuple[str, ...]

    def as_dict(self) -> dict:
        return {"memory_id": self.memory_id, "verdict": self.verdict,
                "reason": self.reason, "changed_sources": list(self.changed_sources),
                "missing_sources": list(self.missing_sources)}


def check_memory(store, memory_id: str) -> MemoryVerdict:
    """Re-derive one memory's grounding against the current store."""
    row = store.memory(memory_id)
    if row is None:
        return MemoryVerdict(memory_id, UNVERIFIABLE, "memory not found", (), ())
    try:
        source_ids, recorded = decode_provenance(
            row["source_ids"], row["source_hashes"])
    except ProvenanceFormatError as exc:
        return MemoryVerdict(memory_id, DRIFT, str(exc), (), ())
    # fail closed: a memory that cites no source has no grounding to verify, so
    # it can never be a definite MATCH.
    if not source_ids:
        return MemoryVerdict(memory_id, UNVERIFIABLE,
                             "memory cites no sources — grounding unconfirmable", (), ())
    # the memory row must reproduce its OWN content hash first (detects a direct
    # edit of the memory's text/sources/criterion in the store)
    try:
        fresh = memory_hash(row["text"], source_ids, row["criterion"])
    except (AttributeError, TypeError, UnicodeError):
        return MemoryVerdict(memory_id, DRIFT,
                             "stored hash does not reproduce (memory row altered)",
                             (), ())
    if fresh != row["content_sha256"]:
        return MemoryVerdict(memory_id, DRIFT,
                             "stored hash does not reproduce (memory row altered)",
                             (), ())
    missing, changed = [], []
    # a source id is either an L0 turn or another memory (L2 cites L1, L3 cites L2)
    for sid in source_ids:
        turn = store.turn(sid)
        cur = turn or store.memory(sid)
        if cur is None:
            missing.append(sid)
            continue
        # Re-hash the source's actual fields. Trusting only content_sha256 lets
        # a direct SQLite edit preserve a stale stored hash and falsely MATCH.
        if turn is not None:
            try:
                fresh_source = content_hash(cur["role"], cur["text"])
            except (AttributeError, TypeError, UnicodeError):
                changed.append(sid)
                continue
        else:
            try:
                current_source_ids, _ = decode_provenance(
                    cur["source_ids"], cur["source_hashes"])
                fresh_source = memory_hash(cur["text"], current_source_ids,
                                           cur["criterion"])
            except (AttributeError, TypeError, UnicodeError, ProvenanceFormatError):
                # Malformed provenance is a content change, not trustworthy
                # evidence that the grounding is still current.
                changed.append(sid)
                continue
        # Compare both the actual fields and the stored address to the original
        # snapshot. This detects changed bytes and a separately forged/stale
        # content_sha256 field.
        if fresh_source != cur["content_sha256"]:
            changed.append(sid)
            continue
        # A present row is always re-hashed before absence of the extraction
        # snapshot is considered. Stale current bytes are provable DRIFT.
        snap = recorded.get(sid)
        if snap is None:
            missing.append(sid)
        elif fresh_source != snap:
            changed.append(sid)
    if changed:
        return MemoryVerdict(memory_id, DRIFT,
                             "source content changed since extraction",
                             tuple(sorted(changed)), tuple(sorted(missing)))
    if missing:
        return MemoryVerdict(memory_id, UNVERIFIABLE,
                             "source(s) gone or unrecorded — grounding unconfirmable",
                             (), tuple(sorted(missing)))
    return MemoryVerdict(memory_id, MATCH, "grounding present and unchanged", (), ())


def drift_report(store, layer: str | None = "L1") -> dict:
    """Verdict every memory (of `layer`, default L1) against the current store.
    Overall folds like the harness: any DRIFT -> DRIFT, else any UNVERIFIABLE ->
    UNVERIFIABLE, else MATCH."""
    verdicts = [check_memory(store, r["id"]) for r in store.memories(layer=layer)]
    vs = [v.verdict for v in verdicts]
    overall = (DRIFT if DRIFT in vs
               else UNVERIFIABLE if UNVERIFIABLE in vs else MATCH)
    return {"schema": "mneme.drift-report/1", "overall": overall,
            "checked": len(verdicts),
            "drifted": [v.memory_id for v in verdicts if v.verdict == DRIFT],
            "unverifiable": [v.memory_id for v in verdicts if v.verdict == UNVERIFIABLE],
            "verdicts": [v.as_dict() for v in verdicts],
            "recheck": "mneme drift --state DB  (re-derive every memory's grounding)"}
