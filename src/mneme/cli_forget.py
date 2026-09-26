"""cli_forget.py: `mneme forget` and `mneme scrub`.

`forget` prints the plan first. Row text (collateral, lineage and duplicate
previews) is shown only when stdout is a terminal or --show-text is given,
because a plan printed to a pipe can land in an agent's model context and
reach its provider. `--dry-run` plans through a read-only connection and
changes nothing, not even an older database's schema stamp. Without --yes the
command asks; with --yes it still refuses collateral and duplicates until
--allow-collateral says the caller saw them. `--emit-opening` prints the salt
of each erase commitment once; the salts are never stored, so run it
yourself rather than through an agent.

`scrub` finishes an erase that stopped after its commit (erase_finish.py).

Exit codes: 0 applied (status `erased` or `erased_sources_kept`) or a dry
run, 1 refused, 2 target or database missing, 3 applied but the receipt names
residue, remaining copies, an incomplete scan or an unverified finish.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from . import erase_finish, snapshot_dir
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
from .store import Store

CLEAN = ("erased", "erased_sources_kept")


def _interactive() -> bool:
    return sys.stdin.isatty()


def _shows_text(args) -> bool:
    return bool(args.show_text) or sys.stdout.isatty()


def _ask(prompt: str) -> str:
    return input(prompt)


def _print(value: dict) -> None:
    print(json.dumps(value, indent=2))


def add_parsers(sub) -> None:
    fg = sub.add_parser(
        "forget", help="erase a memory, turn or session and everything derived from it",
        description="Plan, confirm and apply a true forget. Exit 0 applied, 1 refused, "
                    "2 missing, 3 applied with residue, copies or an unfinished check.")
    fg.add_argument("target", help="a memory id, or a turn id with --turn, or a session "
                                   "name with --session")
    kind = fg.add_mutually_exclusive_group()
    for flag in ("memory", "turn", "session"):
        kind.add_argument(f"--{flag}", dest="kind", action="store_const", const=flag)
    fg.add_argument("--keep-sources", action="store_true",
                    help="keep the source turns of a memory target")
    fg.add_argument("--reason", default="",
                    help="stored verbatim in the audit log, so it may not repeat erased "
                         "text or name an erased row's id or digest")
    mode = fg.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    mode.add_argument("--yes", action="store_true", help="apply without asking")
    fg.add_argument("--allow-collateral", action="store_true",
                    help="with --yes, also erase collateral and duplicate rows")
    fg.add_argument("--show-text", action="store_true",
                    help="show row text in the plan even when stdout is not a terminal")
    fg.add_argument("--plan-sha256", default=None,
                    help="apply only if the plan still has this digest")
    fg.add_argument("--emit-opening", action="store_true",
                    help="print each erase commitment's salt once; it is never stored")
    fg.set_defaults(func=cmd_forget, kind="memory")
    sc = sub.add_parser("scrub", help="finish an erase: remove the store's replay snapshots, "
                                      "checkpoint and VACUUM, and report the files")
    sc.set_defaults(func=cmd_scrub)


def _exists(state: str) -> bool:
    if state != ":memory:" and not Path(state).exists():
        print(f"no mneme database at {state}", file=sys.stderr)
        return False
    return True


def _selection(args) -> Selection:
    field = {"memory": "memories", "turn": "turns", "session": "sessions"}[args.kind]
    return Selection(**{field: (args.target,)}, keep_sources=args.keep_sources)


def _confirmed(plan: dict, args) -> bool:
    counts = plan["counts"]
    extra = counts["collateral"] + counts["duplicates"]
    if args.yes:
        if extra and not args.allow_collateral:
            _print(plan)
            print(f"forget refused: the plan also erases {counts['collateral']} collateral "
                  f"and {counts['duplicates']} duplicate rows; review them above, then "
                  "add --allow-collateral", file=sys.stderr)
            return False
        return True
    _print(plan)
    if not _interactive():
        print("confirmation required: re-run with --yes (plus --allow-collateral when "
              "the plan lists collateral or duplicates), or use --dry-run", file=sys.stderr)
        return False
    try:
        answer = _ask(f"Erase {counts['turns']} turns and "
                      f"{sum(counts['memories'].values())} memory rows ({extra} collateral "
                      "or duplicate)? [y/N] ")
    except EOFError:
        print("\nconfirmation required: no answer was read", file=sys.stderr)
        return False
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
    return 0 if receipt["status"] in CLEAN else 3


def _missing(store, exc: Exception) -> int:
    print(str(exc), file=sys.stderr)
    if erase_finish.is_pending(store.conn):
        print(erase_finish.PENDING_WARNING, file=sys.stderr)
    return 2


def _dry_run(args) -> int:
    store = Store(args.state, read_only=True) if args.state != ":memory:" else Store()
    try:
        plan = plan_erase(store, _selection(args), previews=_shows_text(args))
    except EraseTargetNotFound as exc:
        return _missing(store, exc)
    finally:
        store.close()
    _print(plan)
    return 0


def cmd_forget(args) -> int:
    if not _exists(args.state):
        return 2
    if args.dry_run:
        return _dry_run(args)
    memory = AgentMemory(args.state)
    try:
        selection = _selection(args)
        try:
            plan = plan_erase(memory.store, selection, previews=_shows_text(args))
        except EraseTargetNotFound as exc:
            return _missing(memory.store, exc)
        if not _confirmed(plan, args):
            return 1
        return _apply(memory, args, selection, plan)
    finally:
        memory.close()


def cmd_scrub(args) -> int:
    if not _exists(args.state):
        return 2
    memory = AgentMemory(args.state)
    try:
        result = erase_finish.finish(memory.store, scrub_store)
    finally:
        memory.close()
    _print(result)
    clean = "remedy" not in result and not result["snapshots"]["failed"]
    return 0 if clean else 3
