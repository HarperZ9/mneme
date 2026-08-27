#!/usr/bin/env python3
"""verify_audit.py -- a zero-dependency, standalone verifier for a mneme audit
chain. Pure Python stdlib, no mneme import. A stranger holding only the SQLite
store file re-derives the tamper-evidence offline:

    python verify_audit.py memory.db

It re-reads the append-only audit rows in order and recomputes each entry hash
as sha256 over the x1f-joined parts (prev_hash, op, memory_id, layer, before_sha,
after_sha, reason), the exact bytes mneme's receipt.content_hash produces. A
flipped byte in any row, or a reordered row, snaps the chain. When the store
also records a committed head anchor (meta audit_head + audit_count) it checks
the chain still ends there, so a lopped-off tail is caught too.

Exit 0 = MATCH (chain re-derives), 1 = DRIFT (a row or the head does not
re-derive), 2 = UNVERIFIABLE (the file or the audit table is missing/unreadable).
A store with no head anchor is verified chain-only, mirroring the in-tree check.
"""
import hashlib
import sqlite3
import sys
from pathlib import Path

GENESIS = ""


def _h(*parts):
    m = hashlib.sha256()
    for p in parts:
        m.update(b"\x1f")
        m.update(p.encode("utf-8"))
    return m.hexdigest()


def _connect(db_path):
    path = Path(db_path)
    if not path.is_file():
        return None
    try:
        return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def _has_audit(conn):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='audit'").fetchone()
    return row is not None


def _meta(conn, key):
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _first_break(conn):
    """Walk the rows in ord order; return (label, detail) on the first field that
    does not re-derive, else None. The head anchor, when present, is checked last
    so tail truncation cannot pass as a clean prefix."""
    prev, count = GENESIS, 0
    rows = conn.execute(
        "SELECT ord, op, memory_id, layer, before_sha, after_sha, reason, entry_sha "
        "FROM audit ORDER BY ord").fetchall()
    for ordv, op, mid, layer, before, after, reason, stored in rows:
        prev = _h(prev, op, mid, layer, before, after, reason)
        if prev != stored:
            return "DRIFT", "entry_sha does not re-derive at ord %s" % ordv
        count += 1
    head, expected = _meta(conn, "audit_head"), _meta(conn, "audit_count")
    if head is None or expected is None:
        return None
    if prev != head:
        return "DRIFT", "chain head does not match the committed audit_head anchor"
    if count != int(expected):
        return "DRIFT", "row count %d does not match audit_count %s" % (count, expected)
    return None


def verify(db_path):
    conn = _connect(db_path)
    if conn is None:
        return "UNVERIFIABLE", "store file missing or unreadable", 2
    try:
        if not _has_audit(conn):
            return "UNVERIFIABLE", "no audit table in the store", 2
        broken = _first_break(conn)
    except sqlite3.Error as exc:
        return "UNVERIFIABLE", "store unreadable: %s" % exc, 2
    finally:
        conn.close()
    if broken is not None:
        return broken[0], broken[1], 1
    return "MATCH", "audit chain re-derives clean", 0


def main(argv):
    if not argv:
        print("usage: python verify_audit.py <store.db>", file=sys.stderr)
        return 2
    label, detail, code = verify(argv[0])
    print("%s  %s" % (label, detail))
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
