"""audit_writer.py: append hash-chained audit rows without committing.

The audit log is append-only and hash-chained, with a committed head anchor
(`audit_count`, `audit_head` in meta) so a truncated or emptied log is caught.
Every function here works on a connection and never commits: the caller owns
the transaction. That is what lets an erase delete its rows and write their
audit entries as one atomic change. A crash between the two then leaves
neither, instead of an audit record of a deletion that never happened.

The ordinal is the store-wide monotonic `ord` counter kept in meta, so audit
rows interleave with turns and memories without a wall clock.
"""
from __future__ import annotations

import sqlite3

from .receipt import content_hash


def meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))


def next_ord(conn: sqlite3.Connection) -> int:
    current = meta_get(conn, "ord")
    value = int(current) + 1 if current is not None else 0
    meta_set(conn, "ord", str(value))
    return value


def entry_hash(prev: str, op: str, memory_id: str, layer: str, before: str,
               after: str, reason: str) -> str:
    # each field is a separate \x1f-framed part, so a '|' or any other byte in
    # a free-text field cannot shift across a boundary and forge a collision
    return content_hash(prev, op, memory_id, layer, before, after, reason)


def verify(conn: sqlite3.Connection) -> bool:
    """Re-derive the chain; True iff every entry hash reproduces and, when the
    store has a head anchor, the chain ends at it (count and last entry_sha).
    An unanchored legacy log gets the chain-only check. Reads, never writes."""
    prev, count = "", 0
    for row in conn.execute(
            "SELECT op, memory_id, layer, before_sha, after_sha, reason, entry_sha "
            "FROM audit ORDER BY ord"):
        prev = entry_hash(prev, *row[:6])
        if prev != row[6]:
            return False
        count += 1
    head, expected = meta_get(conn, "audit_head"), meta_get(conn, "audit_count")
    if head is None or expected is None:
        return True
    return prev == head and expected == str(count)


def append(conn: sqlite3.Connection, op: str, memory_id: str, layer: str,
           before: str, after: str, reason: str) -> dict:
    """Append one chained audit row and advance the head anchor. No commit."""
    prev = conn.execute(
        "SELECT entry_sha FROM audit ORDER BY ord DESC LIMIT 1").fetchone()
    entry = entry_hash(prev[0] if prev else "", op, memory_id, layer,
                       before, after, reason)
    conn.execute(
        "INSERT INTO audit(ord,op,memory_id,layer,before_sha,after_sha,reason,entry_sha) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (next_ord(conn), op, memory_id, layer, before, after, reason, entry))
    meta_set(conn, "audit_count", str(int(meta_get(conn, "audit_count") or "0") + 1))
    meta_set(conn, "audit_head", entry)
    return {"op": op, "memory_id": memory_id, "layer": layer,
            "before_sha": before, "after_sha": after, "reason": reason,
            "entry_sha": entry}
