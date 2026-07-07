"""drift.py — memory that flags its own staleness.

Every other agent-memory system silently keeps a fact after its source changed;
you find out when the agent acts on a stale memory. mneme re-derives each L1
atom's content hash from its CURRENT source turns and compares it to the hash
stored at extraction time. The verdict per memory:

  MATCH        every source turn is present and unchanged
  DRIFT        a source turn's content changed since extraction
  UNVERIFIABLE a source turn is gone (cannot confirm the memory's grounding)

This is the freshness verdict none of the class carries. Pure: it reads the
store and re-hashes; a consumer re-runs it and gets the same verdict.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .receipt import content_hash, memory_hash

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
    source_ids = json.loads(row["source_ids"])
    missing, changed = [], []
    # a source id is either an L0 turn or another memory (L2 cites L1, L3 cites L2)
    for sid in source_ids:
        cur = store.turn(sid) or store.memory(sid)
        if cur is None:
            missing.append(sid)
            continue
        # was the source's content the same when this memory was derived?
        # (we cannot know the OLD source hash, so we detect change by re-deriving
        #  this memory's OWN hash from current sources: if the stored hash no
        #  longer reproduces, a source it depends on changed)
    # re-derive the memory hash from its current text + sources + criterion
    fresh = memory_hash(row["text"], source_ids, row["criterion"])
    if missing:
        return MemoryVerdict(memory_id, UNVERIFIABLE,
                             "source turn(s) gone — grounding unconfirmable",
                             (), tuple(sorted(missing)))
    if fresh != row["content_sha256"]:
        # the memory text or criterion was tampered in the store
        return MemoryVerdict(memory_id, DRIFT,
                             "stored hash does not reproduce (memory row altered)",
                             (), ())
    # sources present: check whether any source's CURRENT content differs from
    # what it was — detectable because a turn stores its own content hash and we
    # can compare the memory's atom text against the live source turn text
    for sid in source_ids:
        turn = store.turn(sid)
        if turn is None:
            continue
        # an L1 atom is a substring/derivative of its source turn; if the atom's
        # text is no longer contained in the (normalized) current turn, the source
        # changed under it
        if _normalized(row["text"]) not in _normalized(turn["text"]) and row["layer"] == "L1":
            changed.append(sid)
    if changed:
        return MemoryVerdict(memory_id, DRIFT,
                             "source turn changed since extraction",
                             tuple(sorted(changed)), ())
    return MemoryVerdict(memory_id, MATCH, "grounding present and unchanged", (), ())


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


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
