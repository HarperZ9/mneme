"""scenario.py — L2 scenarios: group related atoms into scene blocks.

The class leader's L2 layer clusters atoms into scenarios with an LLM. mneme's
floor is deterministic: atoms in a session are clustered by shared salient
tokens (a connected-components pass over an atom-similarity graph), so the same
atoms always yield the same scenarios — a scenario you can rebuild bit-for-bit.

Each scenario CITES its member atom ids as provenance, so an L2 scenario is
itself drift-checkable (drift.py treats a memory whose cited source is gone as
UNVERIFIABLE, and L2 inherits that). A scenario whose atoms drift is a scenario
you can no longer trust — surfaced, not hidden.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .receipt import content_hash

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "with",
         "i", "my", "me", "is", "am", "are", "was", "it", "this", "that"}


def _salient(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if len(t) >= 3 and t not in _STOP}


@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    text: str
    atom_ids: tuple[str, ...]
    theme: tuple[str, ...]         # the shared tokens that bind the cluster


def cluster_atoms(atoms: list, *, min_shared: int = 1) -> list[Scenario]:
    """Group atom rows (id, text) into scenarios by shared salient tokens.
    Deterministic: union-find over the atom graph, atoms and clusters ordered by
    id, so the scenario ids and membership are stable across rebuilds."""
    rows = sorted(atoms, key=lambda a: a["id"])
    n = len(rows)
    toks = [_salient(r["text"]) for r in rows]
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    for i in range(n):
        for j in range(i + 1, n):
            if len(toks[i] & toks[j]) >= min_shared:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    scenarios: list[Scenario] = []
    for root in sorted(groups):
        members = sorted(groups[root])
        atom_ids = tuple(rows[i]["id"] for i in members)
        text = "\n".join(rows[i]["text"] for i in members)
        theme = set.intersection(*[toks[i] for i in members]) if len(members) > 1 else toks[members[0]]
        sid = content_hash("scenario", *atom_ids)[:16]
        scenarios.append(Scenario(id=sid, text=text, atom_ids=atom_ids,
                                  theme=tuple(sorted(theme))))
    return scenarios
