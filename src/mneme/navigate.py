"""navigate.py: recall by walking a topic outline, with the path in the receipt.

At each inner node the walk scores every child against the query (BM25, with
the siblings as the corpus) and follows each child whose score is above zero
and at least ``ratio`` times the best sibling's score. Only memories in the
leaves it reaches are ranked; the ranking uses the same corpus-wide BM25 as
flat keyword recall. The receipt lists every node visited, every child scored
and whether it was followed, so "why was this memory never considered" has a
recorded answer. ``verify_navigation`` replays the walk from the rows.
"""
from __future__ import annotations

from .outline import Node, build_outline, outline_sha256
from .recall import BM25, _tok
from .receipt import content_hash

SCHEMA = "mneme.navrecall/1"
DEFAULTS = {"top_k": 5, "ratio": 0.5, "leaf_size": 12, "fanout": 6}


def _child_text(node: Node, toks: list[list[str]]) -> list[str]:
    out: list[str] = []
    for i in node.members:
        out.extend(toks[i])
    return out


def _step(node: Node, qtok: list[str], toks: list[list[str]], ratio: float) -> dict:
    bm = BM25([_child_text(c, toks) for c in node.children])
    scores = [bm.score(qtok, k) for k in range(len(node.children))]
    top = max(scores) if scores else 0.0
    scored = [{"child": c.node_id, "label": c.label, "size": len(c.members),
               "score": round(s, 6), "followed": s > 0 and s >= ratio * top}
              for c, s in zip(node.children, scores)]
    return {"node": node.node_id, "label": node.label, "scored": scored}


def walk(root: Node, query: str, toks: list[list[str]], ratio: float):
    """Breadth-first walk. Returns (reached member indices, recorded path)."""
    qtok = _tok(query)
    if root.is_leaf:
        return sorted(root.members), []
    path, reached, frontier = [], [], [root]
    while frontier:
        nxt = []
        for node in frontier:
            step = _step(node, qtok, toks, ratio)
            path.append(step)
            followed = {s["child"] for s in step["scored"] if s["followed"]}
            for child in node.children:
                if child.node_id not in followed:
                    continue
                if child.is_leaf:
                    reached.extend(child.members)
                else:
                    nxt.append(child)
        frontier = nxt
    return sorted(reached), path


def _params(top_k, ratio, leaf_size, fanout) -> dict:
    if not 0.0 <= ratio <= 1.0:
        raise ValueError("ratio must be within [0, 1]")
    return {"top_k": top_k, "ratio": ratio, "leaf_size": leaf_size, "fanout": fanout}


def navigate(query: str, rows: list, *, top_k: int = 5, ratio: float = 0.5,
             leaf_size: int = 12, fanout: int = 6, outline: Node | None = None) -> dict:
    """Rank the memories a topic-outline walk reaches. Returns a receipt dict.

    ``rows`` are mappings with ``id`` and ``text`` (the same rows flat recall
    takes). ``outline`` defaults to ``build_outline`` over the rows; pass one
    only for controls and tests, since a supplied outline is not re-derivable
    from the rows alone.
    """
    params = _params(top_k, ratio, leaf_size, fanout)
    ids = [r["id"] for r in rows]
    texts = [r["text"] for r in rows]
    root = outline or build_outline(texts, leaf_size=leaf_size, fanout=fanout)
    toks = [_tok(t) for t in texts]
    reached, path = walk(root, query, toks, ratio)
    bm = BM25(toks)
    qtok = _tok(query)
    scored = [(bm.score(qtok, i), ids[i], i) for i in reached]
    scored.sort(key=lambda s: (-s[0], s[1]))
    hits = [{"memory_id": mid, "text": texts[i], "bm25": round(s, 6)}
            for s, mid, i in scored[:top_k] if s > 0]
    return {"schema": SCHEMA, "query": query, "params": params,
            "corpus_size": len(rows), "reached": len(reached),
            "outline_sha256": outline_sha256(root, ids, texts), "path": path, "hits": hits,
            "def_sha256": content_hash(SCHEMA, "bm25(k1=1.5,b=0.75)",
                                       f"follow: score>0 and >= {ratio}*top sibling")}


def verify_navigation(receipt: dict, rows: list) -> bool:
    """Replay the walk from ``rows`` and confirm path, reach and hits match."""
    p = receipt.get("params") or {}
    try:
        fresh = navigate(receipt["query"], rows, **{k: p[k] for k in DEFAULTS})
    except (KeyError, TypeError, ValueError):
        return False
    keys = ("schema", "corpus_size", "reached", "outline_sha256", "path", "hits",
            "def_sha256")
    return all(fresh[k] == receipt.get(k) for k in keys)
