"""erase_finish.py: the marker for an erase that has not finished, and `mneme scrub`.

An erase commits its deletes and audit entries in one transaction, and that
transaction also sets `meta.erase_pending`. The scrub, the snapshot removal
and the receipt come after the commit; when they finish, the marker is
cleared. If the process stops in between (Ctrl-C during a long VACUUM, a
crash, a locked file), the marker stays, `mneme status` and `mneme doctor`
warn, and `mneme scrub` finishes the work: it removes this store's replay
snapshots, checkpoints and vacuums, and clears the marker once both succeed.
"""
from __future__ import annotations

import sqlite3

from .audit_writer import meta_get, meta_set
from .erase_receipt import db_path, remove_snapshots
from .schema import META_ERASE_PENDING

PENDING_WARNING = ("an erase committed but did not finish its scrub and snapshot "
                   "removal; run `mneme scrub` to finish it")


def mark_pending(conn: sqlite3.Connection) -> None:
    """Set the marker inside the erase transaction. No commit."""
    meta_set(conn, META_ERASE_PENDING, "1")


def is_pending(conn: sqlite3.Connection) -> bool:
    try:
        return meta_get(conn, META_ERASE_PENDING) is not None
    except sqlite3.Error:
        return False


def clear_pending(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM meta WHERE key=?", (META_ERASE_PENDING,))
    conn.commit()


def finish(store, scrub) -> dict:
    """`mneme scrub`: remove this store's snapshots, scrub, clear the marker."""
    conn = store.conn
    snapshots = remove_snapshots(conn, db_path(conn))
    result = scrub(store)
    result["snapshots"] = snapshots
    if is_pending(conn):
        if "remedy" in result or snapshots["failed"]:
            result["erase_pending"] = "still_set"
        else:
            clear_pending(conn)
            result["erase_pending"] = "cleared"
    return result
