"""erase.py: true forget. Remove the raw turn and every derived form, provably.

`plan_erase` (erase_plan.py) computes what goes. `apply_erase` recomputes the
plan under the write lock, refuses a digest that no longer matches, and then,
in one transaction with `secure_delete` on, deletes every row in the plan and
appends one audit entry per erased row. The entry names a random erase ref,
never the content-derived id, and its `before` value is a commitment
`sha256("mneme.erase.v1" || 0x00 || salt || utf8(text))` whose 32-byte salt is
never stored. Only the local CLI can print the salt, once, on request. The
same transaction deletes the salts of the erased memories' blinded update and
supersede history (audit_blind.py), so those earlier entries link to nothing.

After the commit, `scrub_store` checkpoints a WAL database and runs VACUUM
with `temp_store=MEMORY` (so the transient copy stays out of the OS temp
directory). The receipt (erase_receipt.py) then removes this store's replay
snapshots, scans the database files for erased bytes, and names what the
erase could not reach.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import audit_blind, audit_writer
from .erase_plan import EraseTargetNotFound, Selection, plan_erase
from .erase_receipt import build_receipt, legacy_audit_rows

__all__ = ["CollateralError", "EraseTargetNotFound", "ErasedTextInReasonError",
           "Selection", "StalePlanError", "apply_erase", "forget_memory",
           "plan_erase", "scrub_store"]

COMMITMENT_TAG = b"mneme.erase.v1"
REASON_RUN = 16
CHECKPOINT_RETRY_SECONDS = 5.0
SCRUB_REMEDY = "close other connections to this database, then run `mneme scrub`"


class CollateralError(ValueError):
    """The erase would also remove memories the caller did not name."""

    def __init__(self, plan: dict):
        super().__init__(
            f"forget would also erase {plan['counts']['collateral']} collateral "
            "memory rows that share a source turn with the target; review "
            "`plan` and pass allow_collateral=True to erase them too")
        self.plan = plan


class StalePlanError(ValueError):
    """The store changed after the plan was made."""


class ErasedTextInReasonError(ValueError):
    """The reason would store erased text verbatim in the audit log."""


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


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _repeats(said: str, windows: set[str], erased: str) -> bool:
    if len(erased) >= REASON_RUN:
        return any(window in erased for window in windows)
    # a short text must appear whole, not inside a longer word ("hi" in "this")
    return re.search(rf"(?<!\w){re.escape(erased)}(?!\w)", said) is not None


def check_reason(reason: str, texts) -> None:
    """Refuse a reason holding an erased text, or any 16-character run of one."""
    said = _norm(reason)
    windows = {said[i:i + REASON_RUN] for i in range(len(said) - REASON_RUN + 1)}
    for text in texts:
        erased = _norm(text)
        if erased and _repeats(said, windows, erased):
            raise ErasedTextInReasonError(
                "the reason repeats text that this erase removes; reasons are "
                "stored verbatim in the audit log, so give the reason without it")


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


def _erase_in_transaction(store, selection: Selection, expected: str, reason: str) -> dict:
    plan = plan_erase(store, selection)
    if plan["plan_sha256"] != expected:
        raise StalePlanError(
            "stale plan: the store changed since the plan was made; plan again "
            "and confirm the new plan_sha256")
    subjects = _subjects(store.conn, plan)
    check_reason(reason, [s.text for s in subjects]
                 + [x for s in subjects for x in s.locators()])
    legacy = legacy_audit_rows(store.conn, subjects)
    openings = _delete_and_audit(store.conn, subjects, reason)
    salts = audit_blind.delete_salts(store.conn, plan["memories"])
    texts = [s.text for s in subjects] + [s.origin for s in subjects if s.origin]
    return {"plan": plan, "texts": texts, "legacy": legacy, "openings": openings,
            "salts_deleted": salts}


def _pragma(conn: sqlite3.Connection, name: str) -> int:
    return int(conn.execute(f"PRAGMA {name}").fetchone()[0])


def apply_erase(store, selection: Selection, expected_sha256: str, *,
                reason: str = "", emit_openings: bool = False) -> dict:
    """Apply exactly the plan whose digest the caller confirmed."""
    if store.read_only:
        raise ValueError("erase needs a writable store; this one was opened read-only")
    conn = store.conn
    if conn.in_transaction:
        raise RuntimeError("erase refused: the store has an open transaction; "
                           "commit or roll it back first")
    prior = {name: _pragma(conn, name) for name in ("secure_delete", "temp_store")}
    conn.execute("PRAGMA secure_delete=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            work = _erase_in_transaction(store, selection, expected_sha256, reason)
            _commit(conn)
        except BaseException:
            conn.rollback()
            raise
        scrub = scrub_store(store)
    finally:
        for name, value in prior.items():
            conn.execute(f"PRAGMA {name}={value}")
    return build_receipt(store, work, scrub, reason=reason, emit_openings=emit_openings)


def forget_memory(store, memory_id: str, reason: str = "", *, include_sources: bool = True,
                  allow_collateral: bool = False) -> dict | None:
    """The library forget: plan, refuse unconsented collateral, apply."""
    selection = Selection(memories=(memory_id,), keep_sources=not include_sources)
    try:
        plan = plan_erase(store, selection, previews=True)
    except EraseTargetNotFound:
        return None
    if plan["collateral"] and not allow_collateral:
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
