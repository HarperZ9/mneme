"""outline.py: a deterministic topic outline over stored memories.

The outline is a tree. Each inner node splits its members by salient terms:
the term shared by the most members (but not by nearly all of them) becomes a
child, its members leave the pool, and the next term is chosen from the rest.
Members no term claims go to an "other" child. A node with few members is a
leaf. The same rows always give the same tree, so a navigation path recorded
against it can be replayed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .receipt import content_hash
from .scenario import _salient

OTHER = "(other)"


@dataclass(frozen=True, slots=True)
class Node:
    node_id: str
    label: str
    members: tuple[int, ...]
    children: tuple["Node", ...] = field(default=())

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def as_dict(self, ids: list[str]) -> dict:
        out = {"node": self.node_id, "label": self.label,
               "members": [ids[i] for i in self.members]}
        if self.children:
            out["children"] = [c.as_dict(ids) for c in self.children]
        return out


def _pick_term(pool: set[int], toks: list[set[str]], cap: float) -> str | None:
    """The salient term held by the most pool members, ties broken by the term."""
    df: dict[str, int] = {}
    for i in pool:
        for t in toks[i]:
            df[t] = df.get(t, 0) + 1
    usable = [(n, t) for t, n in df.items() if 2 <= n <= cap]
    if not usable:
        return None
    usable.sort(key=lambda nt: (-nt[0], nt[1]))
    return usable[0][1]


def _groups(members: tuple[int, ...], toks: list[set[str]],
            fanout: int, cap_share: float) -> list[tuple[str, tuple[int, ...]]]:
    pool = set(members)
    cap = cap_share * len(members)
    groups: list[tuple[str, tuple[int, ...]]] = []
    while pool and len(groups) < fanout - 1:
        term = _pick_term(pool, toks, cap)
        if term is None:
            break
        claimed = tuple(sorted(i for i in pool if term in toks[i]))
        pool -= set(claimed)
        groups.append((term, claimed))
    if pool:
        groups.append((OTHER, tuple(sorted(pool))))
    return groups


def _build(members: tuple[int, ...], node_id: str, label: str, toks: list[set[str]],
           opts: dict, depth: int) -> Node:
    if len(members) <= opts["leaf_size"] or depth >= opts["max_depth"]:
        return Node(node_id, label, members)
    groups = _groups(members, toks, opts["fanout"], opts["cap_share"])
    if len(groups) < 2:
        return Node(node_id, label, members)
    children = tuple(_build(g, f"{node_id}.{k}", term, toks, opts, depth + 1)
                     for k, (term, g) in enumerate(groups))
    return Node(node_id, label, members, children)


def build_outline(texts: list[str], *, leaf_size: int = 12, fanout: int = 6,
                  max_depth: int = 4, cap_share: float = 0.8) -> Node:
    """Build the outline over ``texts`` (row order is the member index)."""
    if leaf_size < 1 or fanout < 2:
        raise ValueError("leaf_size must be >= 1 and fanout >= 2")
    toks = [_salient(t) for t in texts]
    opts = {"leaf_size": leaf_size, "fanout": fanout, "max_depth": max_depth,
            "cap_share": cap_share}
    return _build(tuple(range(len(texts))), "r", "(root)", toks, opts, 0)


def outline_sha256(root: Node, ids: list[str], texts: list[str]) -> str:
    """Hash of the tree shape, membership and member text, so a receipt names
    the exact outline it walked and an edited memory changes the hash."""
    keyed = [f"{i}:{content_hash(t)}" for i, t in zip(ids, texts)]
    return content_hash("mneme.outline/1",
                        json.dumps(root.as_dict(keyed), sort_keys=True))


def leaves(root: Node) -> list[Node]:
    if root.is_leaf:
        return [root]
    out: list[Node] = []
    for c in root.children:
        out.extend(leaves(c))
    return out


def deal_to_leaves(root: Node, order: list[int]) -> Node:
    """Same tree shape, members re-dealt to leaves in ``order``.

    A control: if a random deal navigates as well as the real outline, the
    outline is doing no work.
    """
    it = iter(order)

    def rebuild(node: Node) -> Node:
        if node.is_leaf:
            return Node(node.node_id, node.label,
                        tuple(sorted(next(it) for _ in node.members)))
        kids = tuple(rebuild(c) for c in node.children)
        merged = tuple(sorted(i for k in kids for i in k.members))
        return Node(node.node_id, node.label, merged, kids)

    return rebuild(root)
