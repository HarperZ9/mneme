"""audit_blind.py: blinded audit history for update, supersede and row-level forget.

Up to schema 4 these operations wrote a version's content hash into the
append-only audit log. The hash of a known text is easy to recompute, so the
entry could confirm a guess of what a memory once said, and it outlived any
later erase. From schema 5 each value is a commitment

    "b1:" + sha256("mneme.audit-blind.v1" || 0x00 || salt || utf8(content_sha256))

with a fresh 32-byte salt. The salt is kept in the `salts` table under the
memory the value describes, so while the memory lives its owner can open the
history (`opens`). An erase deletes those salts in the same transaction as the
rows, and the commitments then link to nothing. A row-level forget deletes the
row, so its tombstone salt is never stored and the row's earlier salts go too.

Each operation changes the row, stores its salts and appends its audit entry
in one transaction, and rolls all of it back on any failure.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from collections.abc import Iterable

from . import audit_writer
from .receipt import memory_hash

BLIND_TAG = b"mneme.audit-blind.v1"
BLIND_PREFIX = "b1:"


def is_blinded(value: str) -> bool:
    return value.startswith(BLIND_PREFIX)


def _commit_to(salt: bytes, content: str) -> str:
    body = BLIND_TAG + b"\x00" + salt + content.encode("utf-8")
    return BLIND_PREFIX + hashlib.sha256(body).hexdigest()


def record(conn: sqlite3.Connection, subject_id: str, content: str) -> str:
    """A commitment to `content` whose salt is stored under `subject_id`. No commit."""
    salt = secrets.token_bytes(32)
    value = _commit_to(salt, content)
    conn.execute("INSERT INTO salts(value, subject_id, salt) VALUES(?,?,?)",
                 (value, subject_id, salt.hex()))
    return value


def unopenable(content: str) -> str:
    """A commitment whose salt is never stored, for a row that is going away."""
    return _commit_to(secrets.token_bytes(32), content)


def opens(conn: sqlite3.Connection, value: str, content: str) -> bool:
    """True iff `value` is a stored commitment to `content`."""
    if not is_blinded(value):
        return False
    row = conn.execute("SELECT salt FROM salts WHERE value=?", (value,)).fetchone()
    if row is None:
        return False
    return hmac.compare_digest(_commit_to(bytes.fromhex(row[0]), content), value)


def delete_salts(conn: sqlite3.Connection, subject_ids: Iterable[str]) -> int:
    """Delete every salt stored under the given memories. No commit."""
    deleted = 0
    for subject_id in subject_ids:
        deleted += conn.execute("DELETE FROM salts WHERE subject_id=?",
                                (subject_id,)).rowcount
    return deleted


def _atomically(conn: sqlite3.Connection, change) -> dict:
    try:
        entry = change()
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return entry


def supersede(store, old_id: str, new_id: str, reason: str = "") -> dict | None:
    """Close a memory's validity, keeping it for history (see Store.supersede)."""
    row = store.memory(old_id)
    if row is None or row["valid_until"] is not None:
        return None
    conn = store.conn

    def change() -> dict:
        at = audit_writer.next_ord(conn)
        conn.execute("UPDATE memories SET valid_until=?, superseded_by=? WHERE id=?",
                     (at, new_id, old_id))
        before = record(conn, old_id, row["content_sha256"])
        return audit_writer.append(conn, "supersede", old_id, row["layer"], before, "",
                                   reason or f"superseded by {new_id}")
    return _atomically(conn, change)


def update(store, memory_id: str, new_text: str, reason: str = "") -> dict | None:
    """Replace a memory's text and content hash, keeping its provenance."""
    row = store.memory(memory_id)
    if row is None:
        return None
    conn = store.conn
    after_sha = memory_hash(new_text, json.loads(row["source_ids"]), row["criterion"])

    def change() -> dict:
        conn.execute("UPDATE memories SET text=?, content_sha256=? WHERE id=?",
                     (new_text, after_sha, memory_id))
        before = record(conn, memory_id, row["content_sha256"])
        after = record(conn, memory_id, after_sha)
        return audit_writer.append(conn, "update", memory_id, row["layer"],
                                   before, after, reason)
    return _atomically(conn, change)


def forget(store, memory_id: str, reason: str = "") -> dict | None:
    """Row-level delete with a blinded tombstone (see Store.forget)."""
    row = store.memory(memory_id)
    if row is None:
        return None
    conn = store.conn

    def change() -> dict:
        conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        delete_salts(conn, (memory_id,))
        return audit_writer.append(conn, "forget", memory_id, row["layer"],
                                   unopenable(row["content_sha256"]), "", reason)
    return _atomically(conn, change)
