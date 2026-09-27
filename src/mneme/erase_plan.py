"""erase_plan.py: what a true forget removes, computed before anything is deleted.

A selection names memories, turns or sessions. The plan is the closure:

  1. Seed turns: the named turns, the turns of the named sessions, and the
     source turns of every named memory, of its supersession lineage (the
     same fact at another time) and of the near-duplicates consolidation
     merged into it, unless the caller keeps sources.
  2. Memories: the named ones, every memory that cites an erased turn or an
     erased memory, and the supersession lineage of each erased memory in both
     directions, repeated until nothing changes. Scenario and persona rows cite
     atoms, so they fall inside this closure.
  3. Collateral: memories erased only because they share a source turn with a
     named memory, computed as the difference between the closure with and
     without the source turns.
  4. Duplicates: turns and memories of the same users that repeat an erased
     text whole (erase_text.py), with everything derived from them, repeated
     until no new duplicate appears. Turns the caller keeps are left out.

Collateral and duplicates are listed so a caller can consent to them. The
digest binds the sorted ids, the options, and the content hash of every row
in the plan. Apply recomputes the plan under the write lock and refuses a
digest that no longer matches, so an edit between plan and confirm is caught.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .erase_index import Index, closure, duplicates, existing, lineage_closure, turn_rows

PLAN_SCHEMA = "mneme.erase-plan/1"
MAX_DUPLICATE_ROUNDS = 8


@dataclass(frozen=True)
class Selection:
    memories: tuple[str, ...] = ()
    turns: tuple[str, ...] = ()
    sessions: tuple[str, ...] = ()
    keep_sources: bool = False


class EraseTargetNotFound(LookupError):
    """A named memory, turn or session is not in the store."""


def _named(store, index: Index, selection: Selection) -> tuple[set[str], set[str]]:
    if not (selection.memories or selection.turns or selection.sessions):
        raise ValueError("nothing selected: name a memory, a turn or a session")
    for memory_id in selection.memories:
        if memory_id not in index.rows:
            raise EraseTargetNotFound(f"no memory with id {memory_id!r}")
    turns = existing(store, "turns", "id", selection.turns)
    for turn_id in sorted(set(selection.turns) - turns):
        raise EraseTargetNotFound(f"no turn with id {turn_id!r}")
    session_turns = existing(store, "turns", "session", selection.sessions)
    session_memories = existing(store, "memories", "session", selection.sessions)
    for session in selection.sessions:
        if not (existing(store, "turns", "session", [session])
                or existing(store, "memories", "session", [session])):
            raise EraseTargetNotFound(f"no turn or memory in session {session!r}")
    return set(selection.memories) | session_memories, turns | session_turns


def _grow(index: Index, turns: dict, named: set[str], seeds: set[str], base: set[str],
          kept_sources: set[str]) -> tuple[set[str], set[str], set[str]]:
    """Add duplicates until none is new: (memories, turns, duplicate turns)."""
    memories, all_turns = set(base), set(seeds)
    extra_memories, dup_turns = set(), set()
    for _ in range(MAX_DUPLICATE_ROUNDS):
        texts = ([index.rows[m].text for m in memories]
                 + [turns[t][0] for t in all_turns if t in turns])
        users = {index.rows[m].user for m in memories}
        new_turns, new_memories = duplicates(
            index, turns, texts, skip_turns=all_turns | kept_sources,
            skip_memories=memories, users=users)
        if not (new_turns or new_memories):
            break
        dup_turns |= new_turns
        extra_memories |= new_memories
        all_turns |= new_turns
        memories = closure(index, named | extra_memories, all_turns)
    return memories, all_turns, dup_turns


def _counts(index: Index, memories, turns, collateral, lineage, dups) -> dict:
    layers: dict[str, int] = {}
    for memory_id in memories:
        layer = index.rows[memory_id].layer
        layers[layer] = layers.get(layer, 0) + 1
    return {"turns": len(turns), "memories": dict(sorted(layers.items())),
            "collateral": len(collateral), "lineage": len(lineage),
            "duplicates": len(dups["turns"]) + len(dups["memories"]),
            "users": len({index.rows[m].user for m in memories})}


def digest(plan: dict, content: dict[str, str]) -> str:
    """Binds the plan's ids and options and each row's content hash. The hashes
    stay out of the returned plan, which an MCP result carries to a model."""
    bound = {key: plan[key] for key in
             ("schema", "targets", "keep_sources", "turns", "memories", "collateral",
              "duplicates", "kept_sources", "merged_away")}
    bound["content"] = content
    text = json.dumps(bound, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plan_erase(store, selection: Selection, *, previews: bool = False) -> dict:
    """Plan an erase without changing the store. Raises EraseTargetNotFound."""
    index, turns = Index(store), turn_rows(store)
    named_memories, named_turns = _named(store, index, selection)
    same_fact = lineage_closure(index, set(selection.memories))
    merged_away, merged_sources = index.merged_away(same_fact)
    fact_sources = {s for m in same_fact for s in index.rows[m].sources} | merged_sources
    fact_turns = existing(store, "turns", "id", fact_sources)
    kept_sources = fact_turns if selection.keep_sources else set()
    seeds = set(named_turns) | (set() if selection.keep_sources else fact_turns)
    base = closure(index, named_memories, seeds)
    memories, all_turns, dup_turns = _grow(index, turns, named_memories, seeds, base,
                                           kept_sources)
    collateral = base - closure(index, named_memories, named_turns)
    dups = {"turns": sorted(dup_turns), "memories": sorted(memories - base)}
    lineage = same_fact - set(selection.memories)
    plan = {
        "schema": PLAN_SCHEMA,
        "targets": {"memories": sorted(set(selection.memories)),
                    "turns": sorted(set(selection.turns)),
                    "sessions": sorted(set(selection.sessions))},
        "keep_sources": bool(selection.keep_sources),
        "turns": sorted(all_turns), "memories": sorted(memories),
        "collateral": sorted(collateral), "lineage": sorted(lineage),
        "duplicates": dups, "kept_sources": sorted(kept_sources),
        "merged_away": sorted(merged_away),
        "counts": _counts(index, memories, all_turns, collateral, lineage, dups),
        "users": sorted({index.rows[m].user for m in memories}),
    }
    content = {**{m: index.rows[m].content_sha256 for m in memories},
               **{t: turns[t][1] for t in all_turns}}
    plan["plan_sha256"] = digest(plan, content)
    if previews:
        shown = collateral | lineage | set(dups["memories"])
        plan["previews"] = {**{m: index.rows[m].text for m in sorted(shown)},
                            **{t: turns[t][0] for t in dups["turns"]}}
    return plan
