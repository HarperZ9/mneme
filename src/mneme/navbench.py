"""navbench.py: flat recall against navigation recall on a fixed query set.

For each query the bench records whether the answering memory is in the top
five under flat keyword recall and under navigation recall, and how many
memories navigation reached. A control re-deals the memories to random leaves
of the same outline shape; if that control also holds recall, the outline is
doing no work. Deterministic for a given seed.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from .navigate import navigate
from .outline import build_outline, deal_to_leaves
from .recall import recall

FIXTURE = "navrecall_corpus.json"


def load_corpus(path: str | Path) -> tuple[list[dict], list[dict]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = [{"id": m["id"], "text": m["text"], "layer": "L1"} for m in data["memories"]]
    return rows, data["queries"]


def _hit_rate(found: list[bool]) -> float:
    return round(sum(found) / len(found), 4) if found else 0.0


def _nav_arm(rows, queries, outline, top_k: int) -> dict:
    found, reached = [], []
    for q in queries:
        r = navigate(q["q"], rows, top_k=top_k, outline=outline)
        found.append(q["id"] in {h["memory_id"] for h in r["hits"]})
        reached.append(r["reached"])
    return {"recall_at_k": _hit_rate(found), "mean_reached": round(sum(reached) / len(reached), 2),
            "found": found}


def run_navbench(rows: list[dict], queries: list[dict], *, top_k: int = 5,
                 seed: int = 20261003) -> dict:
    """Return flat, navigation and random-outline control numbers, per query."""
    flat = [q["id"] in {h.memory_id for h in
                        recall(q["q"], rows, strategy="keyword", top_k=top_k).hits}
            for q in queries]
    outline = build_outline([r["text"] for r in rows])
    nav = _nav_arm(rows, queries, outline, top_k)
    order = list(range(len(rows)))
    random.Random(seed).shuffle(order)
    control = _nav_arm(rows, queries, deal_to_leaves(outline, order), top_k)
    per_query = [{"q": q["q"], "answer": q["id"], "flat": f, "nav": n, "control": c}
                 for q, f, n, c in zip(queries, flat, nav.pop("found"), control.pop("found"))]
    return {"schema": "mneme.navbench/1", "top_k": top_k, "seed": seed,
            "corpus_size": len(rows), "queries": len(queries),
            "flat": {"recall_at_k": _hit_rate(flat), "mean_reached": float(len(rows))},
            "navigation": nav, "control_random_outline": control,
            "per_query": per_query}


def control_spread(rows: list[dict], queries: list[dict], seeds: range,
                   *, top_k: int = 5) -> dict:
    """Random-outline control over many seeds: how often a random deal holds recall.

    One seed can be lucky either way. The spread says whether the outline's
    topic structure, rather than the walk alone, is what holds recall.
    """
    outline = build_outline([r["text"] for r in rows])
    rates = []
    for seed in seeds:
        order = list(range(len(rows)))
        random.Random(seed).shuffle(order)
        arm = _nav_arm(rows, queries, deal_to_leaves(outline, order), top_k)
        rates.append(arm["recall_at_k"])
    return {"seeds": len(rates), "mean_recall_at_k": round(sum(rates) / len(rates), 4),
            "min": min(rates), "max": max(rates), "rates": rates}


def main(argv: list[str] | None = None) -> int:
    import sys
    args = argv if argv is not None else sys.argv[1:]
    default = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / FIXTURE
    rows, queries = load_corpus(args[0] if args else default)
    out = run_navbench(rows, queries)
    out["control_spread"] = control_spread(rows, queries, range(20))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
