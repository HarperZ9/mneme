"""cli_forget.py: `mneme forget` and `mneme scrub` on the owner's terminal.

`forget` prints the plan first. Collateral text is shown here because the
terminal is local; the MCP tool shows ids and counts instead. It asks before it
applies unless `--yes` is given, and with `--yes` it still refuses collateral
until `--allow-collateral` says the caller saw it. `--emit-opening` prints the
salt of each erase commitment once; the salts are never stored.

Exit codes: 0 applied (or a dry run), 1 refused, 2 target or database missing,
3 applied but the scrub or the residual scan found residue.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from . import snapshot_dir
from .erase import (
    EraseTargetNotFound,
    ErasedTextInReasonError,
    Selection,
    StalePlanError,
    apply_erase,
    plan_erase,
    scrub_store,
)
from .memory import AgentMemory


def _interactive() -> bool:
    return sys.stdin.isatty()


def _ask(prompt: str) -> str:
    return input(prompt)


def _print(value: dict) -> None:
    print(json.dumps(value, indent=2))


def add_parsers(sub) -> None:
    fg = sub.add_parser(
        "forget", help="erase a memory, turn or session and everything derived from it",
        description="Plan, confirm and apply a true forget. Exit 0 applied, 1 refused, "
                    "2 missing, 3 applied with residue found.")
    fg.add_argument("target", help="a memory id, or a turn id with --turn, or a session "
                                   "name with --session")
    kind = fg.add_mutually_exclusive_group()
    for flag in ("memory", "turn", "session"):
        kind.add_argument(f"--{flag}", dest="kind", action="store_const", const=flag)
    fg.add_argument("--keep-sources", action="store_true",
                    help="keep the source turns of a memory target")
    fg.add_argument("--reason", default="",
                    help="stored verbatim in the audit log, so it may not repeat erased text")
    mode = fg.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    mode.add_argument("--yes", action="store_true", help="apply without asking")
    fg.add_argument("--allow-collateral", action="store_true",
                    help="with --yes, also erase memories that only share a source turn")
    fg.add_argument("--plan-sha256", default=None,
                    help="apply only if the plan still has this digest")
    fg.add_argument("--emit-opening", action="store_true",
                    help="print each erase commitment's salt once; it is never stored")
    fg.set_defaults(func=cmd_forget, kind="memory")
    sc = sub.add_parser("scrub", help="checkpoint and VACUUM the store and report its files")
    sc.set_defaults(func=cmd_scrub)


def _open(state: str) -> AgentMemory | None:
    if state != ":memory:" and not Path(state).exists():
        print(f"no mneme database at {state}", file=sys.stderr)
        return None
    return AgentMemory(state)


def _selection(args) -> Selection:
    field = {"memory": "memories", "turn": "turns", "session": "sessions"}[args.kind]
    return Selection(**{field: (args.target,)}, keep_sources=args.keep_sources)


def _confirmed(plan: dict, args) -> bool:
    collateral = plan["counts"]["collateral"]
    if args.yes:
        if collateral and not args.allow_collateral:
            _print(plan)
            print(f"forget refused: the plan also erases {collateral} collateral memory "
                  "rows; review them above, then add --allow-collateral", file=sys.stderr)
            return False
        return True
    _print(plan)
    if not _interactive():
        print("confirmation required: re-run with --yes (plus --allow-collateral when "
              "the plan lists collateral), or use --dry-run", file=sys.stderr)
        return False
    counts = plan["counts"]
    answer = _ask(f"Erase {counts['turns']} turns and {sum(counts['memories'].values())} "
                  f"memory rows ({collateral} collateral)? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


def _apply(memory: AgentMemory, args, selection: Selection, plan: dict) -> int:
    try:
        receipt = apply_erase(memory.store, selection, args.plan_sha256 or plan["plan_sha256"],
                              reason=args.reason, emit_openings=args.emit_opening)
    except (StalePlanError, ErasedTextInReasonError, LookupError, ValueError,
            RuntimeError, sqlite3.Error) as exc:
        print(f"forget refused: {exc}", file=sys.stderr)
        return 1
    legacy = snapshot_dir.legacy_temp_snapshots()
    if legacy:
        receipt["legacy_temp_snapshot_paths"] = [str(p) for p in legacy]
    _print(receipt)
    return 0 if receipt["status"] == "erased" else 3


def cmd_forget(args) -> int:
    memory = _open(args.state)
    if memory is None:
        return 2
    try:
        selection = _selection(args)
        try:
            plan = plan_erase(memory.store, selection, previews=True)
        except EraseTargetNotFound as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.dry_run:
            _print(plan)
            return 0
        if not _confirmed(plan, args):
            return 1
        return _apply(memory, args, selection, plan)
    finally:
        memory.close()


def cmd_scrub(args) -> int:
    memory = _open(args.state)
    if memory is None:
        return 2
    try:
        result = scrub_store(memory.store)
    finally:
        memory.close()
    _print(result)
    return 0 if "remedy" not in result else 3
