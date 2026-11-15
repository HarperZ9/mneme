"""cli_subject.py: `mneme forget-subject` and `mneme verify-erasure`.

`forget-subject` erases every record of one user (erase_subject.py). It
prints the plan as counts, never row text, then asks; `--yes` applies without
asking and `--dry-run` only plans, through a read-only connection. When the
erase would also remove another user's rows it refuses until
`--allow-collateral`. The receipt goes to stdout. `--emit-opening` adds the
salt of the subject commitment, once and only to a terminal, by the same rule
as `mneme forget --emit-opening`: keep it with the receipt to show later which
user the receipt is about.

`verify-erasure` checks a saved receipt against the store: the receipt's own
hash, the audit chain, the subject entry and every erase entry it names. With
`--subject` and `--salt` it also checks that the receipt is about that user
and that none of their rows is in the store now.

Exit codes of `forget-subject`: 0 applied (status `erased`) or a dry run,
1 refused, 2 user or database missing, 3 applied with residue, copies or an
unfinished check. `verify-erasure`: 0 every check passed, 1 a check failed,
2 the receipt or database is missing or unreadable.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from . import erase_subject
from .cli_forget import CLEAN, _ask, _exists, _interactive, _print, _stdout_is_terminal
from .erase import CollateralError, EraseTargetNotFound, ErasedTextInReasonError, StalePlanError
from .memory import AgentMemory
from .store import Store

COST = ("The erase rewrites the whole database file and scans it, so it takes "
        "seconds on a store of thousands of memories (see `mneme forget --help`).")


def add_parsers(sub) -> None:
    fs = sub.add_parser(
        "forget-subject",
        help="erase every record of one user and print a hashed erasure receipt",
        description="Erase every memory and turn of one user from this store, with one "
                    "audit entry and a hashed receipt that `mneme verify-erasure` can "
                    "check. The receipt records what mneme removed from this store; it "
                    "does not make the operator compliant with a data-protection law. "
                    + COST + " Exit 0 applied, 1 refused, 2 missing, 3 applied with "
                    "residue, copies or an unfinished check.")
    fs.add_argument("user", help="the user whose records to erase")
    fs.add_argument("--reason", default="",
                    help="stored verbatim in the audit log, so it may not name the user "
                         "or repeat erased text")
    mode = fs.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    mode.add_argument("--yes", action="store_true", help="apply without asking")
    fs.add_argument("--allow-collateral", action="store_true",
                    help="also erase rows of other users that share a turn with this user")
    fs.add_argument("--keep-exports", action="store_true",
                    help="leave export files mneme wrote in place; the receipt reports them")
    fs.add_argument("--emit-opening", action="store_true",
                    help="print the salt of the subject commitment once, to a terminal "
                         "only; it is never stored")
    fs.set_defaults(func=cmd_forget_subject)
    ve = sub.add_parser(
        "verify-erasure",
        help="check a subject erasure receipt against this store's audit chain",
        description="Exit 0 when every check passes, 1 when one fails, 2 when the "
                    "receipt or database cannot be read.")
    ve.add_argument("receipt", help="path of the receipt JSON, or - for stdin")
    ve.add_argument("--subject", default=None, help="the user the receipt should be about")
    ve.add_argument("--salt", default=None, help="the subject salt printed by --emit-opening")
    ve.set_defaults(func=cmd_verify_erasure)


def _plan(args) -> tuple[int, dict | None]:
    store = Store(args.state, read_only=True) if args.state != ":memory:" else Store()
    try:
        return 0, erase_subject.plan_subject(store, args.user)
    except EraseTargetNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 2, None
    finally:
        store.close()


def _confirmed(plan: dict, args) -> bool:
    if plan["other_users"] and not args.allow_collateral:
        print(f"forget-subject refused: the plan also erases rows of {plan['other_users']} "
              "other user(s) that share a turn with this user; add --allow-collateral",
              file=sys.stderr)
        return False
    if args.yes:
        return True
    if not _interactive():
        print("confirmation required: re-run with --yes, or use --dry-run", file=sys.stderr)
        return False
    counts = plan["counts"]
    try:
        answer = _ask(f"Erase {counts['turns']} turns and "
                      f"{sum(counts['memories'].values())} memory rows of this user? [y/N] ")
    except EOFError:
        print("\nconfirmation required: no answer was read", file=sys.stderr)
        return False
    return answer.strip().lower() in ("y", "yes")


def cmd_forget_subject(args) -> int:
    if not _exists(args.state):
        return 2
    if args.emit_opening and not _stdout_is_terminal():
        print("forget-subject refused: --emit-opening writes the salt only to a terminal, "
              "and stdout is not one", file=sys.stderr)
        return 1
    code, plan = _plan(args)
    if plan is None:
        return code
    _print(plan)
    if args.dry_run:
        return 0
    if not _confirmed(plan, args):
        return 1
    memory = AgentMemory(args.state)
    try:
        receipt = erase_subject.forget_subject(
            memory.store, args.user, reason=args.reason,
            allow_collateral=args.allow_collateral, keep_exports=args.keep_exports,
            emit_opening=args.emit_opening, expected_sha256=plan["plan_sha256"])
    except (StalePlanError, ErasedTextInReasonError, CollateralError, LookupError,
            ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"forget-subject refused: {exc}", file=sys.stderr)
        return 1
    finally:
        memory.close()
    _print(receipt)
    return 0 if receipt["status"] in CLEAN else 3


def cmd_verify_erasure(args) -> int:
    if not _exists(args.state):
        return 2
    try:
        text = sys.stdin.read() if args.receipt == "-" else Path(args.receipt).read_text(
            encoding="utf-8")
        receipt = json.loads(text)
        if not isinstance(receipt, dict):
            raise ValueError("the receipt is not a JSON object")
    except (OSError, ValueError) as exc:
        print(f"verify-erasure failed: {exc}", file=sys.stderr)
        return 2
    store = Store(args.state, read_only=True)
    try:
        result = erase_subject.verify(store, receipt, user=args.subject, salt=args.salt)
    finally:
        store.close()
    _print(result)
    return 0 if result["ok"] else 1
