"""mneme CLI: remember, recall, drift, persona, provenance — accountable memory
from the terminal, each command printing a receipt as JSON."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .memory import AgentMemory


def _load_turns(path: str) -> list[dict]:
    text = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    data = json.loads(text)
    turns = data["turns"] if isinstance(data, dict) else data
    if not isinstance(turns, list):
        raise SystemExit("remember: expected a JSON list of {role,text} turns")
    return turns


def cmd_remember(args) -> int:
    mem = AgentMemory(args.state)
    summary = mem.remember(args.session, _load_turns(args.turns), user=args.user)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_recall(args) -> int:
    mem = AgentMemory(args.state, embed=getattr(args, "embed", None))
    receipt = mem.recall(args.query, strategy=args.strategy, top_k=args.top_k,
                         recency_weight=getattr(args, "recency", 0.0),
                         user=getattr(args, "user", None), session=getattr(args, "session", None),
                         layer=getattr(args, "layer", None), as_of=getattr(args, "as_of", None))
    if args.json:
        print(json.dumps(receipt.as_dict(), indent=2))
    else:
        for h in receipt.hits:
            print(f"[{h.fused:.4f}] {h.text}")
        if not receipt.hits:
            print("(no memories matched)")
    return 0


def cmd_drift(args) -> int:
    mem = AgentMemory(args.state)
    report = mem.drift(layer=args.layer)
    print(json.dumps(report, indent=2))
    return 0 if report["overall"] == "MATCH" else 1


def cmd_persona(args) -> int:
    mem = AgentMemory(args.state)
    print(json.dumps(mem.persona(args.session, user=getattr(args, "user", "")), indent=2))
    return 0


def cmd_provenance(args) -> int:
    mem = AgentMemory(args.state)
    prov = mem.provenance(args.memory_id)
    if prov is None:
        print(f"no memory with id {args.memory_id!r}", file=sys.stderr)
        return 2
    print(json.dumps(prov, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mneme", description="accountable agent memory")
    p.add_argument("--version", action="version", version=f"mneme {__version__}")
    p.add_argument("--state", default="mneme.db",
                   help="path to the SQLite memory DB (default: mneme.db)")
    sub = p.add_subparsers(dest="command", required=True)

    rem = sub.add_parser("remember", help="record turns (L0) and extract atoms (L1) with provenance")
    rem.add_argument("session")
    rem.add_argument("turns", help="path to a JSON list of {role,text} turns (or - for stdin)")
    rem.add_argument("--user", default="", help="scope the memory to one user (multi-tenant)")
    rem.set_defaults(func=cmd_remember)

    rec = sub.add_parser("recall", help="retrieve memories with a re-derivable ranking receipt")
    rec.add_argument("query")
    rec.add_argument("--strategy", choices=["keyword", "vector", "hybrid"], default="hybrid")
    rec.add_argument("--top-k", type=int, default=5)
    rec.add_argument("--embed", choices=["ngram"], default=None, help="turn on the zero-dep local vector channel")
    rec.add_argument("--recency", type=float, default=0.0, help="weight recent memories (0=off; transparent, in the receipt)")
    rec.add_argument("--user", default=None, help="scope recall to one user")
    rec.add_argument("--session", default=None, help="scope recall to one session")
    rec.add_argument("--layer", default=None, help="scope recall to one layer (default L1)")
    rec.add_argument("--as-of", type=int, default=None, dest="as_of",
                     help="recall against the memory state at ordinal N (point-in-time)")
    rec.add_argument("--json", action="store_true")
    rec.set_defaults(func=cmd_recall)

    dr = sub.add_parser("drift", help="verdict every memory's grounding (MATCH/DRIFT/UNVERIFIABLE)")
    dr.add_argument("--layer", default="L1")
    dr.set_defaults(func=cmd_drift)

    per = sub.add_parser("persona", help="synthesize a persona (L3) grounded in its atoms")
    per.add_argument("session")
    per.add_argument("--user", default="", help="scope the persona to one user (multi-tenant)")
    per.set_defaults(func=cmd_persona)

    prov = sub.add_parser("provenance", help="show a memory's provenance receipt")
    prov.add_argument("memory_id")
    prov.set_defaults(func=cmd_provenance)

    sc = sub.add_parser("scenarios", help="cluster a session's atoms into L2 scene blocks")
    sc.add_argument("session")
    sc.add_argument("--min-shared", type=int, default=1)
    sc.add_argument("--user", default="", help="scope the scenarios to one user (multi-tenant)")
    sc.set_defaults(func=cmd_scenarios)

    fg = sub.add_parser("forget", help="delete a memory, leaving an auditable tombstone")
    fg.add_argument("memory_id")
    fg.add_argument("--reason", default="")
    fg.set_defaults(func=cmd_forget)

    up = sub.add_parser("update", help="edit a memory's text, recording before/after in the audit log")
    up.add_argument("memory_id")
    up.add_argument("text")
    up.add_argument("--reason", default="")
    up.set_defaults(func=cmd_update)

    au = sub.add_parser("audit", help="show the hash-chained history of every forget/update")
    au.set_defaults(func=cmd_audit)

    insp = sub.add_parser("inspect", help="render a self-contained white-box HTML view of the memory")
    insp.add_argument("--out", default=None, help="write the HTML here (default: stdout)")
    insp.set_defaults(func=cmd_inspect)

    ing = sub.add_parser("ingest", help="ingest gather-shaped intake items (JSON) with source provenance")
    ing.add_argument("session")
    ing.add_argument("items", help="JSON list of {id,text,source,ref,method,sha256} (or - for stdin)")
    ing.set_defaults(func=cmd_ingest)

    ch = sub.add_parser("chain", help="walk a memory's full provenance chain back to its web source")
    ch.add_argument("memory_id")
    ch.set_defaults(func=cmd_chain)

    sup = sub.add_parser("supersede", help="record that a fact CHANGED (keeps the old for history)")
    sup.add_argument("memory_id")
    sup.add_argument("text")
    sup.add_argument("--reason", default="")
    sup.set_defaults(func=cmd_supersede)

    hist = sub.add_parser("history", help="the timeline of a fact (current + superseded)")
    hist.add_argument("--contains", default=None)
    hist.add_argument("--predicate", default=None)
    hist.add_argument("--user", default=None)
    hist.set_defaults(func=cmd_history)

    eg = sub.add_parser("entity-graph", help="build a grounded entity graph (typed relations + named entities)")
    eg.add_argument("--user", default=None)
    eg.add_argument("--session", default=None)
    eg.set_defaults(func=cmd_entity_graph)

    cons = sub.add_parser("consolidate", help="merge near-duplicate memories (audit-tombstoned); surface contradictions")
    cons.add_argument("--session", default=None)
    cons.add_argument("--user", default=None, help="scope consolidation to one user (never merges across users)")
    cons.add_argument("--plan", action="store_true", help="show the plan without applying it")
    cons.set_defaults(func=cmd_consolidate)

    xc = sub.add_parser("to-crucible", help="export schema-v2 drift measurements for Crucible assessment")
    xc.add_argument("--session", default=None)
    xc.add_argument("--layer", default="L1")
    xc.set_defaults(func=cmd_to_crucible)

    bench = sub.add_parser("bench", help="token-economics benchmark: reduction AND answer-recall, re-derivable")
    bench.add_argument("--turns", default=None, help="JSON conversation file (default: built-in scenario)")
    bench.add_argument("--probes", default=None, help="JSON probes file [{query,answer_contains}]")
    bench.add_argument("--top-k", type=int, default=3)
    bench.add_argument("--strategy", choices=["keyword", "vector", "hybrid"], default="keyword")
    bench.set_defaults(func=cmd_bench)

    mcp = sub.add_parser("mcp", help="serve mneme over MCP stdio (agent memory tools)")
    mcp.set_defaults(func=cmd_mcp)
    return p


def cmd_forget(args) -> int:
    entry = AgentMemory(args.state).forget(args.memory_id, reason=args.reason)
    if entry is None:
        print(f"no memory with id {args.memory_id!r}", file=sys.stderr)
        return 2
    print(json.dumps(entry, indent=2))
    return 0


def cmd_update(args) -> int:
    entry = AgentMemory(args.state).update(args.memory_id, args.text, reason=args.reason)
    if entry is None:
        print(f"no memory with id {args.memory_id!r}", file=sys.stderr)
        return 2
    print(json.dumps(entry, indent=2))
    return 0


def cmd_audit(args) -> int:
    print(json.dumps(AgentMemory(args.state).audit(), indent=2))
    return 0


def cmd_ingest(args) -> int:
    text = sys.stdin.read() if args.items == "-" else open(args.items, encoding="utf-8").read()
    items = json.loads(text)
    print(json.dumps(AgentMemory(args.state).ingest_gather(args.session, items), indent=2))
    return 0


def cmd_chain(args) -> int:
    chain = AgentMemory(args.state).provenance_chain(args.memory_id)
    if chain is None:
        print(f"no memory with id {args.memory_id!r}", file=sys.stderr)
        return 2
    print(json.dumps(chain, indent=2))
    return 0


def cmd_supersede(args) -> int:
    r = AgentMemory(args.state).supersede(args.memory_id, args.text, reason=args.reason)
    if r is None:
        print("no such memory, or already superseded", file=sys.stderr); return 2
    print(json.dumps(r, indent=2)); return 0


def cmd_history(args) -> int:
    print(json.dumps(AgentMemory(args.state).history(contains=args.contains, predicate=args.predicate, user=args.user), indent=2)); return 0


def cmd_entity_graph(args) -> int:
    print(json.dumps(AgentMemory(args.state).entity_graph(user=args.user, session=args.session), indent=2))
    return 0


def cmd_consolidate(args) -> int:
    r = AgentMemory(args.state).consolidate(args.session, apply=not args.plan,
                                            user=getattr(args, "user", None))
    print(json.dumps(r, indent=2))
    return 0


def cmd_to_crucible(args) -> int:
    print(json.dumps(AgentMemory(args.state).to_crucible(args.session, args.layer), indent=2))
    return 0


def cmd_inspect(args) -> int:
    from .inspect import build_inspect, render_inspect_html
    page = render_inspect_html(build_inspect(AgentMemory(args.state)))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(page)
        print(f"memory inspector -> {args.out}", file=sys.stderr)
    else:
        print(page)
    return 0


def cmd_bench(args) -> int:
    from .bench import run_bench
    turns = json.load(open(args.turns, encoding="utf-8")) if args.turns else None
    probes = json.load(open(args.probes, encoding="utf-8")) if args.probes else None
    report = run_bench(turns, probes, top_k=args.top_k, strategy=args.strategy)
    print(json.dumps(report, indent=2))
    return 0


def cmd_scenarios(args) -> int:
    mem = AgentMemory(args.state)
    print(json.dumps(mem.build_scenarios(args.session, min_shared=args.min_shared,
                                         user=getattr(args, "user", "")), indent=2))
    return 0


def cmd_mcp(args) -> int:
    from .mcp import serve
    return serve()


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
