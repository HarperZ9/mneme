"""erase_index.py: the graph an erase plan walks.

`Index` reads every memory row once, with who cites what, the supersession
links, and the merge links consolidation writes (`merges` table): when a
near-duplicate is merged away, its row goes but its source turns stay, so
the kept memory carries them as lineage. `duplicates` finds rows that repeat
an erased text whole (erase_text.repeats_whole) and belong to the same users.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .erase_text import norm, repeats_whole

SUPERSEDE_EXTRACTOR = "supersede/v1"


@dataclass
class Row:
    id: str
    layer: str
    user: str
    session: str | None
    text: str
    sources: tuple[str, ...]
    superseded_by: str | None
    extractor: str
    content_sha256: str


def cited(raw: str) -> tuple[str, ...]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


class Index:
    def __init__(self, store):
        conn = store.conn
        self.rows: dict[str, Row] = {}
        self.citers: dict[str, set[str]] = {}
        self.predecessors: dict[str, set[str]] = {}
        self.merged: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
        for r in conn.execute(
                'SELECT id, layer, "user", session, text, source_ids, superseded_by, '
                "extractor, content_sha256 FROM memories"):
            row = Row(r[0], r[1], r[2], r[3], r[4], cited(r[5]), r[6], r[7], r[8])
            self.rows[row.id] = row
            for source in row.sources:
                self.citers.setdefault(source, set()).add(row.id)
            if row.superseded_by:
                self.predecessors.setdefault(row.superseded_by, set()).add(row.id)
        if has_table(conn, "merges"):
            for kept, dropped, sources in conn.execute(
                    "SELECT kept_id, dropped_id, source_ids FROM merges"):
                self.merged.setdefault(kept, []).append((dropped, cited(sources)))

    def lineage(self, memory_id: str) -> set[str]:
        row = self.rows[memory_id]
        linked = set(self.predecessors.get(memory_id, ()))
        if row.superseded_by in self.rows:
            linked.add(row.superseded_by)
        if row.extractor == SUPERSEDE_EXTRACTOR:
            linked |= {s for s in row.sources if s in self.rows}
        return linked

    def merged_away(self, memory_ids) -> tuple[set[str], set[str]]:
        """The ids merged into these memories, through any chain, and their sources."""
        dropped: set[str] = set()
        sources: set[str] = set()
        frontier = set(memory_ids)
        while frontier:
            found = {(d, s) for m in frontier for d, s in self.merged.get(m, ())}
            new = {d for d, _s in found} - dropped
            dropped |= new
            sources |= {x for _d, s in found for x in s}
            frontier = new
        return dropped, sources


def lineage_closure(index: Index, seeds: set[str]) -> set[str]:
    found, frontier = set(seeds), set(seeds)
    while frontier:
        frontier = {m for f in frontier for m in index.lineage(f)} - found
        found |= frontier
    return found


def closure(index: Index, memories: set[str], turns: set[str]) -> set[str]:
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


def existing(store, table: str, column: str, values) -> set[str]:
    values, found = sorted(set(values)), set()
    for start in range(0, len(values), 500):
        chunk = values[start:start + 500]
        marks = ",".join("?" * len(chunk))
        found |= {r[0] for r in store.conn.execute(
            f"SELECT id FROM {table} WHERE {column} IN ({marks})", chunk)}
    return found


def turn_rows(store) -> dict[str, tuple[str, str]]:
    """Every turn as id -> (text, content_sha256)."""
    return {r[0]: (r[1], r[2]) for r in store.conn.execute(
        "SELECT id, text, content_sha256 FROM turns")}


def duplicates(index: Index, turns: dict, texts, *, skip_turns: set[str],
               skip_memories: set[str], users: set[str]) -> tuple[set[str], set[str]]:
    """Turns and memories, outside the skip sets, that repeat one of `texts`.

    A memory counts only when its user is one of `users`. A turn counts only
    when every memory citing it belongs to one of `users`; no user column sits
    on a turn, so a turn nothing cites counts for any user."""
    needles = {n for n in (norm(t) for t in texts) if n}
    if not needles:
        return set(), set()

    def hit(text: str) -> bool:
        row = norm(text)
        return any(repeats_whole(n, row) for n in needles)

    found_memories = {m for m, row in index.rows.items()
                      if m not in skip_memories and row.user in users and hit(row.text)}
    found_turns = set()
    for turn_id, (text, _sha) in turns.items():
        owners = {index.rows[c].user for c in index.citers.get(turn_id, ())}
        if turn_id not in skip_turns and owners <= users and hit(text):
            found_turns.add(turn_id)
    return found_turns, found_memories
