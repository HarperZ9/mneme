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
    summary = mem.remember(args.session, _load_turns(args.turns))
    print(json.dumps(summary, indent=2))
    return 0


def cmd_recall(args) -> int:
    mem = AgentMemory(args.state)
    receipt = mem.recall(args.query, strategy=args.strategy, top_k=args.top_k)
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
    print(json.dumps(mem.persona(args.session), indent=2))
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
    rem.set_defaults(func=cmd_remember)

    rec = sub.add_parser("recall", help="retrieve memories with a re-derivable ranking receipt")
    rec.add_argument("query")
    rec.add_argument("--strategy", choices=["keyword", "vector", "hybrid"], default="hybrid")
    rec.add_argument("--top-k", type=int, default=5)
    rec.add_argument("--json", action="store_true")
    rec.set_defaults(func=cmd_recall)

    dr = sub.add_parser("drift", help="verdict every memory's grounding (MATCH/DRIFT/UNVERIFIABLE)")
    dr.add_argument("--layer", default="L1")
    dr.set_defaults(func=cmd_drift)

    per = sub.add_parser("persona", help="synthesize a persona (L3) grounded in its atoms")
    per.add_argument("session")
    per.set_defaults(func=cmd_persona)

    prov = sub.add_parser("provenance", help="show a memory's provenance receipt")
    prov.add_argument("memory_id")
    prov.set_defaults(func=cmd_provenance)

    sc = sub.add_parser("scenarios", help="cluster a session's atoms into L2 scene blocks")
    sc.add_argument("session")
    sc.add_argument("--min-shared", type=int, default=1)
    sc.set_defaults(func=cmd_scenarios)

    bench = sub.add_parser("bench", help="token-economics benchmark: reduction AND answer-recall, re-derivable")
    bench.add_argument("--turns", default=None, help="JSON conversation file (default: built-in scenario)")
    bench.add_argument("--probes", default=None, help="JSON probes file [{query,answer_contains}]")
    bench.add_argument("--top-k", type=int, default=3)
    bench.add_argument("--strategy", choices=["keyword", "vector", "hybrid"], default="keyword")
    bench.set_defaults(func=cmd_bench)

    mcp = sub.add_parser("mcp", help="serve mneme over MCP stdio (agent memory tools)")
    mcp.set_defaults(func=cmd_mcp)
    return p


def cmd_bench(args) -> int:
    from .bench import run_bench
    turns = json.load(open(args.turns, encoding="utf-8")) if args.turns else None
    probes = json.load(open(args.probes, encoding="utf-8")) if args.probes else None
    report = run_bench(turns, probes, top_k=args.top_k, strategy=args.strategy)
    print(json.dumps(report, indent=2))
    return 0


def cmd_scenarios(args) -> int:
    mem = AgentMemory(args.state)
    print(json.dumps(mem.build_scenarios(args.session, min_shared=args.min_shared), indent=2))
    return 0


def cmd_mcp(args) -> int:
    from .mcp import serve
    return serve()


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
