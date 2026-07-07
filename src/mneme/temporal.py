"""temporal.py — memory with a timeline, backed by an auditable history.

Mem0's largest benchmark gain is temporal reasoning: "where did the user live
BEFORE Seattle". mneme answers it without guessing, because superseding a fact
keeps the old one with a validity window (created_ord .. valid_until) instead of
erasing it. The timeline of any fact is then a query, and every transition is in
the hash-chained audit log — a memory history you can re-check, which no memory
product carries.

Two erasure semantics, kept distinct on purpose:
  - SUPERSEDE  a fact CHANGED — the old value is retained for history.
  - FORGET     a fact must be ERASED (GDPR) — the text is removed, only a
               tombstone remains. History cannot resurrect a forgotten fact.

`history(...)` returns the ordered timeline of matching memories with their
validity windows; `as_of` (on recall/memories) reconstructs what was known at a
point in time.
"""
from __future__ import annotations

import re


def _matches(row, contains: str | None, predicate: str | None) -> bool:
    text = row["text"].lower()
    if contains and contains.lower() not in text:
        return False
    if predicate:
        from .entity import relations_in
        preds = {r["predicate"] for r in relations_in(row["text"])}
        if predicate not in preds:
            return False
    return True


def history(memory, *, contains: str | None = None, predicate: str | None = None,
            user: str | None = None) -> dict:
    """The timeline of matching memories (current + superseded), oldest first,
    each with its validity window and what superseded it. `predicate` filters by
    entity relation (e.g. 'lives_in'); `contains` by substring."""
    rows = memory.store.memories(user=user, include_superseded=True)
    timeline = []
    for r in rows:
        if r["layer"] != "L1" or not _matches(r, contains, predicate):
            continue
        timeline.append({
            "memory_id": r["id"], "text": r["text"],
            "from_ord": r["created_ord"], "until_ord": r["valid_until"],
            "current": r["valid_until"] is None,
            "superseded_by": r["superseded_by"],
        })
    timeline.sort(key=lambda t: t["from_ord"])
    return {
        "schema": "mneme.history/1",
        "filter": {"contains": contains, "predicate": predicate, "user": user},
        "timeline": timeline,
        "transitions": len([t for t in timeline if not t["current"]]),
        "current": next((t["text"] for t in reversed(timeline) if t["current"]), None),
        "note": ("every transition is also in the hash-chained audit log — the "
                 "history is re-checkable. A FORGOTTEN (GDPR-erased) fact never "
                 "appears here; only superseded facts keep their timeline."),
    }
