"""erase_plan.py: what a true forget removes, computed before anything is deleted.

A selection names memories, turns or sessions. The plan is the closure:

  1. Seed turns: the named turns, the turns of the named sessions, and the
     source turns of every named memory and of its supersession lineage (the
     same fact at another time), unless the caller keeps sources.
  2. Memories: the named ones, every memory that cites an erased turn or an
     erased memory, and the supersession lineage of each erased memory in both
     directions, repeated until nothing changes. Scenario and persona rows cite
     atoms, so they fall inside this closure.
  3. Collateral: memories erased only because they share a source turn with a
     named memory. They are computed as the difference between the closure with
     and without the source turns, and listed so a caller can consent to them.

The digest binds the sorted ids and options. Apply recomputes the plan under
the write lock and refuses a digest that no longer matches.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

PLAN_SCHEMA = "mneme.erase-plan/1"
SUPERSEDE_EXTRACTOR = "supersede/v1"


@dataclass(frozen=True)
class Selection:
    memories: tuple[str, ...] = ()
    turns: tuple[str, ...] = ()
    sessions: tuple[str, ...] = ()
    keep_sources: bool = False


class EraseTargetNotFound(LookupError):
    """A named memory, turn or session is not in the store."""


@dataclass
class _Row:
    id: str
    layer: str
    user: str
    session: str | None
    text: str
    sources: tuple[str, ...]
    superseded_by: str | None
    extractor: str


def _cited(raw: str) -> tuple[str, ...]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


class _Index:
    def __init__(self, store):
        self.rows: dict[str, _Row] = {}
        self.citers: dict[str, set[str]] = {}
        self.predecessors: dict[str, set[str]] = {}
        for r in store.conn.execute(
                'SELECT id, layer, "user", session, text, source_ids, superseded_by, '
                "extractor FROM memories"):
            row = _Row(r[0], r[1], r[2], r[3], r[4], _cited(r[5]), r[6], r[7])
            self.rows[row.id] = row
            for source in row.sources:
                self.citers.setdefault(source, set()).add(row.id)
            if row.superseded_by:
                self.predecessors.setdefault(row.superseded_by, set()).add(row.id)

    def lineage(self, memory_id: str) -> set[str]:
        row = self.rows[memory_id]
        linked = set(self.predecessors.get(memory_id, ()))
        if row.superseded_by in self.rows:
            linked.add(row.superseded_by)
        if row.extractor == SUPERSEDE_EXTRACTOR:
            linked |= {s for s in row.sources if s in self.rows}
        return linked


def _lineage_closure(index: _Index, seeds: set[str]) -> set[str]:
    found, frontier = set(seeds), set(seeds)
    while frontier:
        frontier = {m for f in frontier for m in index.lineage(f)} - found
        found |= frontier
    return found


def _closure(index: _Index, memories: set[str], turns: set[str]) -> set[str]:
    erased = set(memories)
    frontier = set(memories) | set(turns)
    while frontier:
        grown: set[str] = set()
        for item in frontier:
            grown |= index.citers.get(item, set())
            if item in index.rows:
                grown |= index.lineage(item)
        frontier = grown - erased
        erased |= frontier
    return erased


def _existing(store, table: str, column: str, values) -> set[str]:
    values, found = sorted(set(values)), set()
    for start in range(0, len(values), 500):
        chunk = values[start:start + 500]
        marks = ",".join("?" * len(chunk))
        found |= {r[0] for r in store.conn.execute(
            f"SELECT id FROM {table} WHERE {column} IN ({marks})", chunk)}
    return found


def _named(store, index: _Index, selection: Selection) -> tuple[set[str], set[str]]:
    if not (selection.memories or selection.turns or selection.sessions):
        raise ValueError("nothing selected: name a memory, a turn or a session")
    for memory_id in selection.memories:
        if memory_id not in index.rows:
            raise EraseTargetNotFound(f"no memory with id {memory_id!r}")
    turns = _existing(store, "turns", "id", selection.turns)
    for turn_id in sorted(set(selection.turns) - turns):
        raise EraseTargetNotFound(f"no turn with id {turn_id!r}")
    session_turns = _existing(store, "turns", "session", selection.sessions)
    session_memories = _existing(store, "memories", "session", selection.sessions)
    for session in selection.sessions:
        if not (_existing(store, "turns", "session", [session])
                or _existing(store, "memories", "session", [session])):
            raise EraseTargetNotFound(f"no turn or memory in session {session!r}")
    return set(selection.memories) | session_memories, turns | session_turns


def _counts(index: _Index, memories: set[str], turns: set[str], collateral, lineage) -> dict:
    layers: dict[str, int] = {}
    for memory_id in memories:
        layer = index.rows[memory_id].layer
        layers[layer] = layers.get(layer, 0) + 1
    return {"turns": len(turns), "memories": dict(sorted(layers.items())),
            "collateral": len(collateral), "lineage": len(lineage)}


def digest(plan: dict) -> str:
    bound = {key: plan[key] for key in
             ("schema", "targets", "keep_sources", "turns", "memories", "collateral")}
    text = json.dumps(bound, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plan_erase(store, selection: Selection, *, previews: bool = False) -> dict:
    """Plan an erase without changing the store. Raises EraseTargetNotFound."""
    index = _Index(store)
    named_memories, named_turns = _named(store, index, selection)
    same_fact = _lineage_closure(index, set(selection.memories))
    seeds = set(named_turns)
    if not selection.keep_sources:
        sources = {s for m in same_fact for s in index.rows[m].sources}
        seeds |= _existing(store, "turns", "id", sources)
    memories = _closure(index, named_memories, seeds)
    collateral = memories - _closure(index, named_memories, named_turns)
    lineage = same_fact - set(selection.memories)
    plan = {
        "schema": PLAN_SCHEMA,
        "targets": {"memories": sorted(set(selection.memories)),
                    "turns": sorted(set(selection.turns)),
                    "sessions": sorted(set(selection.sessions))},
        "keep_sources": bool(selection.keep_sources),
        "turns": sorted(seeds), "memories": sorted(memories),
        "collateral": sorted(collateral), "lineage": sorted(lineage),
        "counts": _counts(index, memories, seeds, collateral, lineage),
        "users": sorted({index.rows[m].user for m in memories}),
    }
    plan["plan_sha256"] = digest(plan)
    if previews:
        plan["previews"] = {m: index.rows[m].text for m in sorted(collateral | lineage)}
    return plan
