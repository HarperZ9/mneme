"""erase_subject.py: erase every record of one user, with a receipt that verifies.

A data subject in mneme is a `user`. `forget_subject` selects every memory of
that user in every layer and every turn that belongs to them, plans the erase
(erase_plan.py), and applies it as one true forget (erase.py). A turn written
from schema 6 carries its user. An older turn has none, so it belongs to the
user when only that user's memories cite it or, when none cites it, when every
memory in its session is theirs (erase_index.py). An older turn that nobody
cites, in a session the user shares with someone else, cannot be attributed:
it stays, the receipt counts it, and the status is not `erased`.

The same transaction appends one `erase_subject` audit entry. It names a
random ref, a commitment `sha256("mneme.erase-subject.v1" || 0x00 || salt ||
utf8(user))` and a digest of the erase refs it covers. The salt is never
stored: the caller gets it once (`emit_opening`) and can later show that the
receipt is about that user. Without the salt the log cannot confirm a guess of
who was erased.

The receipt holds counts, refs, the audit head and the erase receipt. It holds
no erased text, no user name and no path. `receipt_sha256` covers every other
field, and `verify` recomputes it and checks the receipt against the store's
audit chain, so a receipt cannot be edited, and an erasure cannot be dropped
from the log, without the check failing.

What this is not: a statement that the operator has met a legal duty. A store
cannot know the copies, recipients and legal grounds outside it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3

from . import audit_writer
from .erase import CollateralError, apply_erase
from .erase_index import Index, has_table, owned_by, session_users, turn_rows
from .erase_plan import EraseTargetNotFound, Selection, plan_erase
from .erase_reason import ErasedTextInReasonError
from .erase_text import norm

SUBJECT_TAG = b"mneme.erase-subject.v1"
RECEIPT_SCHEMA = "mneme.subject-erasure-receipt/1"
PLAN_SCHEMA = "mneme.subject-erasure-plan/1"
BOUNDARY = ("This receipt records what mneme removed from one store and what it "
            "could not reach. It does not show that the operator met a legal duty: "
            "copies outside the store, recipients of the data and the legal grounds "
            "for erasure are the operator's to handle.")
_UNHASHED = ("receipt_sha256", "opening")


def commitment(salt: bytes, user: str) -> str:
    return hashlib.sha256(SUBJECT_TAG + b"\x00" + salt + user.encode("utf-8")).hexdigest()


def refs_digest(refs) -> str:
    return hashlib.sha256("\n".join(sorted(refs)).encode("ascii")).hexdigest()


def receipt_digest(receipt: dict) -> str:
    body = {k: v for k, v in receipt.items() if k not in _UNHASHED}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _turn_users(conn: sqlite3.Connection) -> dict[str, str | None]:
    columns = {row[1] for row in conn.execute('PRAGMA table_info("turns")')}
    if "user" not in columns:
        return {}
    return {row[0]: row[1] for row in conn.execute('SELECT id, "user" FROM turns')}


def select(store, user: str) -> tuple[Selection, int]:
    """The user's memories and turns, and the count of turns nobody can attribute."""
    conn = store.conn
    memories = [row[0] for row in conn.execute(
        'SELECT id FROM memories WHERE "user"=?', (user,))]
    index, turns, tagged = Index(store), turn_rows(store), _turn_users(conn)
    by_session = session_users(index)
    shared = {index.rows[m].session for m in memories}
    owned, unattributed = set(), 0
    for turn_id, (_text, _sha, session) in turns.items():
        tag = tagged.get(turn_id)
        if tag is not None:
            owned |= {turn_id} if tag == user else set()
        elif owned_by(index, turn_id, session, by_session, {user}):
            owned.add(turn_id)
        elif session in shared and not index.citers.get(turn_id):
            unattributed += 1
    if not memories and not owned:
        raise EraseTargetNotFound("no memory or turn belongs to that user")
    return Selection(memories=tuple(sorted(memories)), turns=tuple(sorted(owned))), unattributed


def plan_subject(store, user: str) -> dict:
    """What a subject erase would remove: counts and the digest, never text."""
    selection, unattributed = select(store, user)
    plan = plan_erase(store, selection)
    return {"schema": PLAN_SCHEMA, "counts": plan["counts"],
            "other_users": len(set(plan["users"]) - {user}),
            "unattributed_turns": unattributed, "plan_sha256": plan["plan_sha256"]}


def _check_reason(reason: str, user: str) -> None:
    name = norm(user)
    if len(name) >= 3 and name in norm(reason):
        raise ErasedTextInReasonError(
            "the reason names the user this erase removes; reasons are stored "
            "verbatim in the audit log, so give the reason without the name")


def _rows_left(conn: sqlite3.Connection, user: str) -> int:
    left = conn.execute('SELECT COUNT(*) FROM memories WHERE "user"=?', (user,)).fetchone()[0]
    if "user" in {row[1] for row in conn.execute('PRAGMA table_info("turns")')}:
        left += conn.execute('SELECT COUNT(*) FROM turns WHERE "user"=?',
                             (user,)).fetchone()[0]
    return left


def _status(inner: dict, extra: list[str]) -> tuple[str, list[str]]:
    findings = [*extra, *inner["findings"]]
    if not extra:
        return inner["status"], findings
    return "erased_residue_found", findings


def forget_subject(store, user: str, *, reason: str = "", allow_collateral: bool = False,
                   keep_exports: bool = False, emit_opening: bool = False,
                   expected_sha256: str | None = None) -> dict:
    """Erase every record of `user` and return the subject erasure receipt.

    Raises EraseTargetNotFound when the user has no rows, CollateralError when
    the erase would also remove another user's rows and `allow_collateral` is
    not set, and what `apply_erase` raises (a stale plan, a refused reason)."""
    selection, unattributed = select(store, user)
    plan = plan_erase(store, selection, previews=True)
    if set(plan["users"]) - {user} and not allow_collateral:
        raise CollateralError(plan)
    _check_reason(reason, user)
    sealed: dict = {}

    def seal(conn: sqlite3.Connection, work: dict) -> None:
        salt = secrets.token_bytes(32)
        sealed.update(ref="es_" + secrets.token_hex(12), salt=salt.hex(),
                      commitment=commitment(salt, user),
                      refs=refs_digest(o["erase_ref"] for o in work["openings"]))
        sealed["entry"] = audit_writer.append(
            conn, "erase_subject", sealed["ref"], "", sealed["commitment"],
            sealed["refs"], reason)

    inner = apply_erase(store, selection, expected_sha256 or plan["plan_sha256"],
                        reason=reason, keep_exports=keep_exports,
                        also_erased=(user,), seal=seal)
    conn = store.conn
    extra = [name for failed, name in (
        (_rows_left(conn, user), "subject_rows_present"),
        (unattributed, "turns_not_attributable")) if failed]
    status, findings = _status(inner, extra)
    receipt = {
        "schema": RECEIPT_SCHEMA, "status": status, "findings": findings,
        "subject": {"ref": sealed["ref"], "commitment": sealed["commitment"]},
        "counts": {**plan["counts"], "unattributed_turns": unattributed},
        "erase_refs": inner["erase_refs"],
        "audit": {"entry_sha": sealed["entry"]["entry_sha"], "erase_refs_sha256": sealed["refs"],
                  "head": audit_writer.meta_get(conn, "audit_head"),
                  "entries": int(audit_writer.meta_get(conn, "audit_count") or 0)},
        "erase": inner, "boundary": BOUNDARY,
    }
    receipt["receipt_sha256"] = receipt_digest(receipt)
    if emit_opening:
        receipt["opening"] = {"subject_salt": sealed["salt"]}
    return receipt


def _audit_row(conn: sqlite3.Connection, ref: str):
    return conn.execute("SELECT op, before_sha, after_sha, entry_sha FROM audit "
                        "WHERE memory_id=?", (ref,)).fetchone()


def verify(store, receipt: dict, *, user: str | None = None,
           salt: str | None = None) -> dict:
    """Check a subject erasure receipt against this store. Reads, never writes.

    With `user` and `salt` (the opening) it also checks that the receipt is
    about that user and that no row of theirs is in the store now."""
    conn = store.conn
    subject, audit = receipt.get("subject", {}), receipt.get("audit", {})
    refs = [r.get("ref", "") for r in receipt.get("erase_refs", [])]
    entry = _audit_row(conn, subject.get("ref", "")) if has_table(conn, "audit") else None
    checks = {
        "receipt_hash": hmac.compare_digest(receipt_digest(receipt),
                                            str(receipt.get("receipt_sha256", ""))),
        "audit_chain": audit_writer.verify(conn),
        "subject_entry": entry is not None and tuple(entry) == (
            "erase_subject", subject.get("commitment"), refs_digest(refs),
            audit.get("entry_sha")),
        "erase_entries": bool(refs) and all(
            (row := _audit_row(conn, ref)) is not None and row[0] == "erase" for ref in refs),
    }
    if user is not None and salt is not None:
        try:
            opened = hmac.compare_digest(commitment(bytes.fromhex(salt), user),
                                         str(subject.get("commitment", "")))
        except ValueError:
            opened = False
        checks["subject_commitment"] = opened
        checks["subject_rows_absent"] = _rows_left(conn, user) == 0
    return {"schema": "mneme.subject-erasure-check/1", "ok": all(checks.values()),
            "checks": checks}
