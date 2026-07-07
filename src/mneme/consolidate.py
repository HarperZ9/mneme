"""consolidate.py — merge redundant memories, surface contradictions, auditably.

Mem0's signature feature is intelligent consolidation: adding a memory updates or
merges related ones instead of piling up duplicates and contradictions. mneme's
deterministic floor does the auditable part of that honestly:

  - REDUNDANT atoms (near-duplicate text, high token overlap) are merged: the
    newest is kept, the rest are forgotten with an audit tombstone. The store
    stops accumulating the same fact restated.
  - CONTRADICTION CANDIDATES (atoms about the same subject with a differing
    value, e.g. "I live in Denver" vs "I live in Seattle") are SURFACED, not
    silently resolved. Resolving a semantic contradiction needs judgment (an LLM
    edge, or the operator); guessing which is true is exactly the failure an
    accountable system refuses. The newest is proposed, the conflict is recorded.

Every merge and supersession lands in the same hash-chained audit log as
forget/update, so consolidation is itself re-checkable — you can see what was
merged away and why. Deterministic; an LLM edge can supply semantic merges.
"""
from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "with",
         "i", "my", "me", "is", "am", "are", "was", "it", "this", "that", "prefer"}
# subject cues: a fact is usually "about" its salient noun (place, food, role...)
_VALUE_HINTS = ("live", "based", "located", "work", "prefer", "like", "allergic", "use")


def _salient(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if len(t) >= 3 and t not in _STOP}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)          # Jaccard


def _subject(text: str) -> str | None:
    """The predicate a fact asserts about, e.g. 'live', 'work', 'allergic' —
    two atoms with the same predicate but different objects contradict."""
    low = text.lower()
    for h in _VALUE_HINTS:
        if re.search(rf"\b{h}", low):
            return h
    return None


def plan_consolidation(atoms: list, *, dup_threshold: float = 0.6) -> dict:
    """Given atom rows (id, text) in ingest order, return a plan:
      merges: [{keep, drop:[...], reason}]  redundant near-duplicates
      contradictions: [{predicate, atoms:[...], newest, note}]  surfaced, not resolved
    Deterministic; ordered by id. Does not mutate anything."""
    rows = list(atoms)                     # keep given (chronological) order
    toks = [_salient(r["text"]) for r in rows]
    subj = [_subject(r["text"]) for r in rows]
    used = set()
    merges = []
    # near-duplicate merge: later atom supersedes an earlier highly-overlapping one
    for i in range(len(rows)):
        if i in used:
            continue
        dupes = []
        for j in range(len(rows)):
            if j != i and j not in used and subj[i] == subj[j] \
                    and _overlap(toks[i], toks[j]) >= dup_threshold:
                dupes.append(j)
        if dupes:
            group = sorted([i] + dupes)
            keep = rows[group[-1]]["id"]           # newest (last in order) wins
            drop = [rows[k]["id"] for k in group if rows[k]["id"] != keep]
            for k in group:
                used.add(k)
            merges.append({"keep": keep, "drop": drop,
                           "reason": f"near-duplicate (overlap >= {dup_threshold})"})
    # contradiction candidates: same predicate, NOT merged (different objects)
    contradictions = []
    by_pred: dict[str, list[int]] = {}
    for i, p in enumerate(subj):
        if p and i not in used:
            by_pred.setdefault(p, []).append(i)
    for pred, idxs in sorted(by_pred.items()):
        if len(idxs) > 1:
            contradictions.append({
                "predicate": pred,
                "atoms": [rows[k]["id"] for k in idxs],
                "newest": rows[max(idxs)]["id"],
                "note": ("same predicate, differing content — a contradiction "
                         "candidate, surfaced for review (not auto-resolved)")})
    return {"merges": merges, "contradictions": contradictions}


def consolidate(memory, session: str | None = None, *, dup_threshold: float = 0.6,
                apply: bool = True) -> dict:
    """Plan (and, by default, apply) consolidation over a session's atoms.
    Applying forgets the superseded near-duplicates with audit tombstones;
    contradictions are always surfaced, never auto-resolved. Returns the report."""
    rows = [{"id": r["id"], "text": r["text"]}
            for r in memory.store.memories(layer="L1", session=session)]
    plan = plan_consolidation(rows, dup_threshold=dup_threshold)
    merged = 0
    if apply:
        for m in plan["merges"]:
            for did in m["drop"]:
                if memory.store.forget(did, reason=f"merged into {m['keep']} ({m['reason']})"):
                    merged += 1
    return {
        "schema": "mneme.consolidation/1",
        "session": session, "applied": apply,
        "merges": plan["merges"], "merged_away": merged,
        "contradictions": plan["contradictions"],
        "note": ("near-duplicates merged with audit tombstones; contradiction "
                 "candidates surfaced for review, never auto-resolved (that needs "
                 "judgment — an LLM edge or the operator)"),
        "recheck": "mneme audit  (every merge is a re-checkable tombstone)",
    }
