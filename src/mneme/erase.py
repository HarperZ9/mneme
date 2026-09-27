"""erase.py: true forget. Remove the raw turn and every derived form, and report
what was checked and what remains.

`plan_erase` (erase_plan.py) computes what goes. `apply_erase` sweeps orphaned
replay snapshots, removes this store's snapshots, then recomputes the plan
under the write lock and refuses a digest that no longer matches. In one
transaction with `secure_delete` on, it deletes every row in the plan, appends
one audit entry per erased row, deletes the salts of the erased memories'
blinded history (audit_blind.py) and their merge links, and sets
`meta.erase_pending` (erase_finish.py). An entry names a random erase ref,
never the content-derived id, and its `before` value is a commitment
`sha256("mneme.erase.v1" || 0x00 || salt || utf8(text))` whose 32-byte salt is
never stored. The salts are returned only when the caller asks
(`emit_openings`); the CLI prints them with --emit-opening and the MCP tool
never asks. The reason is checked first (erase_reason.py).

After the commit, `scrub_store` checkpoints a WAL database and runs VACUUM
with `temp_store=MEMORY` (so the transient copy stays out of the OS temp
directory). The receipt (erase_receipt.py) removes this store's snapshots
again, scans the database files and known copies for erased bytes, and names
what the erase could not reach; then the marker is cleared. A failure after
the commit returns a receipt with status `erased_unverified` instead of an
error, because the rows are already gone; the marker stays for `mneme scrub`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import audit_blind, audit_writer, erase_finish, snapshot_dir
from .erase_plan import EraseTargetNotFound, Selection, plan_erase
from .erase_reason import ErasedTextInReasonError, check_reason
from .erase_receipt import RECEIPT_SCHEMA, build_receipt, db_path, remove_snapshots
from .erase_residue import legacy_audit_rows

__all__ = ["CollateralError", "EraseTargetNotFound", "ErasedTextInReasonError",
           "Selection", "StalePlanError", "apply_erase", "forget_memory",
           "plan_erase", "scrub_store"]

COMMITMENT_TAG = b"mneme.erase.v1"
CHECKPOINT_RETRY_SECONDS = 5.0
SCRUB_REMEDY = "close other connections to this database, then run `mneme scrub`"
UNVERIFIED_REMEDY = ("the rows are deleted, but the scrub, snapshot removal or residual "
                     "scan did not finish; run `mneme scrub`, then `mneme doctor`")
_LOG = logging.getLogger("mneme")


class CollateralError(ValueError):
    """The erase would also remove rows the caller did not name."""

    def __init__(self, plan: dict):
        counts = plan["counts"]
        super().__init__(
            f"forget would also erase {counts['collateral']} collateral memory rows "
            f"that share a source turn with the target and {counts['duplicates']} "
            "duplicate rows that repeat an erased text; review `plan` and pass "
            "allow_collateral=True to erase them too")
        self.plan = plan


class StalePlanError(ValueError):
    """The store changed after the plan was made."""


def _commit(conn: sqlite3.Connection) -> None:
    # a seam: tests put a crash between the audit rows and the commit here
    conn.commit()


@dataclass
class _Subject:
    table: str
    id: str
    layer: str
    text: str
    content_sha256: str
    origin: str = ""

    def locators(self) -> tuple[str, ...]:
        """The origin's ref and source digest: erased too, and never boilerplate."""
        try:
            origin = json.loads(self.origin) if self.origin else {}
        except ValueError:
            return ()
        if not isinstance(origin, dict):
            return ()
        return tuple(str(origin[k]) for k in ("ref", "sha256") if origin.get(k))


def _subjects(conn: sqlite3.Connection, plan: dict) -> list[_Subject]:
    out = []
    for memory_id in plan["memories"]:
        r = conn.execute("SELECT layer, text, content_sha256 FROM memories WHERE id=?",
                         (memory_id,)).fetchone()
        out.append(_Subject("memories", memory_id, r[0], r[1], r[2]))
    for turn_id in plan["turns"]:
        r = conn.execute("SELECT text, content_sha256, origin FROM turns WHERE id=?",
                         (turn_id,)).fetchone()
        out.append(_Subject("turns", turn_id, "L0", r[0], r[1], r[2] or ""))
    return out


def _commitment(text: str) -> tuple[bytes, str]:
    salt = secrets.token_bytes(32)
    body = COMMITMENT_TAG + b"\x00" + salt + text.encode("utf-8")
    return salt, hashlib.sha256(body).hexdigest()


def _delete_and_audit(conn: sqlite3.Connection, subjects, reason: str) -> list[dict]:
    order = {"L3": 0, "L2": 1, "L1": 2, "L0": 3}
    shuffled = list(subjects)
    secrets.SystemRandom().shuffle(shuffled)
    shuffled.sort(key=lambda s: (s.table == "turns", order.get(s.layer, 4)))
    openings = []
    for subject in shuffled:
        conn.execute(f"DELETE FROM {subject.table} WHERE id=?", (subject.id,))
        salt, before = _commitment(subject.text)
        ref = "er_" + secrets.token_hex(12)
        audit_writer.append(conn, "erase", ref, subject.layer, before, "", reason)
        openings.append({"erase_ref": ref, "subject_id": subject.id,
                         "layer": subject.layer, "salt": salt.hex()})
    return openings


def _identifiers(subjects, plan: dict) -> list[str]:
    """Values that could confirm a guess of an erased text if a reason kept them."""
    values = [plan["plan_sha256"], *plan["merged_away"]]
    for subject in subjects:
        values += [subject.id, subject.content_sha256, *subject.locators()]
    return values


def _delete_merges(conn: sqlite3.Connection, plan: dict) -> int:
    memories, dropped, turns = set(plan["memories"]), set(plan["merged_away"]), set(plan["turns"])
    doomed = [d for d, kept, raw in conn.execute(
                  "SELECT dropped_id, kept_id, source_ids FROM merges")
              if kept in memories or d in dropped or set(json.loads(raw or "[]")) & turns]
    for dropped_id in doomed:
        conn.execute("DELETE FROM merges WHERE dropped_id=?", (dropped_id,))
    return len(doomed)


def _erase_in_transaction(store, selection: Selection, expected: str, reason: str) -> dict:
    conn = store.conn
    plan = plan_erase(store, selection)
    if plan["plan_sha256"] != expected:
        raise StalePlanError(
            "stale plan: the store changed since the plan was made; plan again "
            "and confirm the new plan_sha256")
    subjects = _subjects(conn, plan)
    texts = [s.text for s in subjects]
    check_reason(reason, texts + [x for s in subjects for x in s.locators()],
                 _identifiers(subjects, plan))
    legacy = legacy_audit_rows(conn, subjects, plan["merged_away"])
    last_ord = int(audit_writer.meta_get(conn, "ord") or -1)
    openings = _delete_and_audit(conn, subjects, reason)
    salts = audit_blind.delete_salts(conn, plan["memories"])
    _delete_merges(conn, plan)
    erase_finish.mark_pending(conn)
    return {"plan": plan, "subject_texts": texts, "legacy": legacy,
            "texts": texts + [s.origin for s in subjects if s.origin],
            "openings": openings, "salts_deleted": salts, "last_ord_before": last_ord}


def _pragma(conn: sqlite3.Connection, name: str) -> int:
    return int(conn.execute(f"PRAGMA {name}").fetchone()[0])


def _committed(store, selection: Selection, expected: str, reason: str) -> dict:
    conn = store.conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        work = _erase_in_transaction(store, selection, expected, reason)
        _commit(conn)
    except BaseException:
        conn.rollback()
        raise
    return work


def _unverified(work: dict, exc: Exception, emit_openings: bool) -> dict:
    plan = work["plan"]
    receipt = {"schema": RECEIPT_SCHEMA, "status": "erased_unverified",
               "findings": ["post_commit_failure"], "plan_sha256": plan["plan_sha256"],
               "keep_sources": plan["keep_sources"], "counts": plan["counts"],
               "erase_refs": [{"ref": o["erase_ref"], "layer": o["layer"]}
                              for o in work["openings"]],
               "error": exc.__class__.__name__, "remedy": UNVERIFIED_REMEDY}
    if emit_openings:
        receipt["openings"] = work["openings"]
    return receipt


def _after_commit(store, work: dict, reason: str, emit_openings: bool) -> dict:
    try:
        scrub = scrub_store(store)
        receipt = build_receipt(store, work, scrub, reason=reason,
                                emit_openings=emit_openings)
        erase_finish.clear_pending(store.conn)
    except Exception as exc:          # the rows are gone: report it, do not refuse
        _LOG.warning("mneme: erase committed, then %s: %s", exc.__class__.__name__, exc)
        return _unverified(work, exc, emit_openings)
    return receipt


def apply_erase(store, selection: Selection, expected_sha256: str, *,
                reason: str = "", emit_openings: bool = False) -> dict:
    """Apply exactly the plan whose digest the caller confirmed."""
    if store.read_only:
        raise ValueError("erase needs a writable store; this one was opened read-only")
    conn = store.conn
    if conn.in_transaction:
        raise RuntimeError("erase refused: the store has an open transaction; "
                           "commit or roll it back first")
    snapshot_dir.startup_sweep()
    before = remove_snapshots(conn, db_path(conn))
    prior = {name: _pragma(conn, name) for name in ("secure_delete", "temp_store")}
    conn.execute("PRAGMA secure_delete=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    try:
        work = _committed(store, selection, expected_sha256, reason)
        work["snapshots_before"] = before
        return _after_commit(store, work, reason, emit_openings)
    finally:
        for name, value in prior.items():
            conn.execute(f"PRAGMA {name}={value}")


def forget_memory(store, memory_id: str, reason: str = "", *, include_sources: bool = True,
                  allow_collateral: bool = False) -> dict | None:
    """The library forget: plan, refuse unconsented collateral or duplicates, apply."""
    selection = Selection(memories=(memory_id,), keep_sources=not include_sources)
    try:
        plan = plan_erase(store, selection, previews=True)
    except EraseTargetNotFound:
        return None
    if (plan["collateral"] or plan["counts"]["duplicates"]) and not allow_collateral:
        raise CollateralError(plan)
    return apply_erase(store, selection, plan["plan_sha256"], reason=reason)


def _checkpoint(conn: sqlite3.Connection) -> str:
    # a TRUNCATE checkpoint waits for readers through the busy handler, so the
    # retry budget is the busy timeout of this one call
    prior = _pragma(conn, "busy_timeout")
    conn.execute(f"PRAGMA busy_timeout={int(CHECKPOINT_RETRY_SECONDS * 1000)}")
    try:
        busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
    except sqlite3.Error as exc:
        return f"failed: {exc}"
    finally:
        conn.execute(f"PRAGMA busy_timeout={prior}")
    return "complete" if busy == 0 else "busy"


def _file_facts(conn: sqlite3.Connection) -> dict:
    main = next((row[2] for row in conn.execute("PRAGMA database_list")
                 if row[1] == "main"), "")
    wal = Path(f"{main}-wal") if main else None
    journal = Path(f"{main}-journal") if main else None
    return {"freelist_count": _pragma(conn, "freelist_count"),
            "journal": "present" if journal and journal.exists() else "absent",
            "wal_bytes": wal.stat().st_size if wal and wal.exists() else 0}


def scrub_store(store) -> dict:
    """Checkpoint a WAL store, VACUUM without temp files, report file facts."""
    conn = store.conn
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    result = {"journal_mode": mode, "checkpoint": "not_wal", "vacuum": "done"}
    prior = _pragma(conn, "temp_store")
    conn.execute("PRAGMA temp_store=MEMORY")
    try:
        if mode == "wal":
            result["checkpoint"] = _checkpoint(conn)
        try:
            conn.execute("VACUUM")
        except sqlite3.Error as exc:
            result["vacuum"] = f"failed: {exc}"
        if mode == "wal":
            result["checkpoint"] = _checkpoint(conn)
    finally:
        conn.execute(f"PRAGMA temp_store={prior}")
    result.update(_file_facts(conn))
    if result["checkpoint"] not in ("complete", "not_wal") or result["vacuum"] != "done":
        result["remedy"] = SCRUB_REMEDY
    return result
