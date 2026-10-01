"""erase_finish.py: the marker for an erase that has not finished, and `mneme scrub`.

An erase commits its deletes and audit entries in one transaction, and that
transaction also sets `meta.erase_pending`. The scrub, the snapshot removal
and the receipt come after the commit; when they finish, the marker is
cleared (`finished`). If the process stops in between (Ctrl-C during a long
VACUUM, a crash), or a step does not finish (a reader holds the WAL, a file is
locked, a snapshot cannot be removed), the marker stays, `mneme status` and
`mneme doctor` warn, and `mneme scrub` finishes the work: it removes this
store's replay snapshots, checkpoints and vacuums, and clears the marker once
both succeed. The marker says how far the erase got: `committed` until the
receipt is built, then `scanned`. After `committed` the scrub cannot run the
residual scan the erase skipped, because the erased texts are gone, and its
result says so.
"""
from __future__ import annotations

import sqlite3

from .audit_writer import meta_get, meta_set
from .erase_receipt import db_path, remove_snapshots
from .schema import META_ERASE_PENDING

PENDING_WARNING = ("an erase committed but did not finish its scrub and snapshot "
                   "removal; run `mneme scrub` to finish it")
COMMITTED, SCANNED = "committed", "scanned"
SCAN_NOT_RUN = ("the erase that set the marker stopped before its receipt, so no "
                "residual scan ran; the erased texts are no longer known, so this "
                "scrub cannot scan for them")
SCAN_RAN = ("the erase ran its residual scan and returned a receipt before this "
            "scrub; this scrub does not scan again")


def mark_pending(conn: sqlite3.Connection) -> None:
    """Set the marker inside the erase transaction. No commit."""
    meta_set(conn, META_ERASE_PENDING, COMMITTED)


def mark_scanned(conn: sqlite3.Connection) -> None:
    """The receipt is built but a step did not finish: keep the marker."""
    meta_set(conn, META_ERASE_PENDING, SCANNED)
    conn.commit()


def is_pending(conn: sqlite3.Connection) -> bool:
    try:
        return meta_get(conn, META_ERASE_PENDING) is not None
    except sqlite3.Error:
        return False


def clear_pending(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM meta WHERE key=?", (META_ERASE_PENDING,))
    conn.commit()


def finished(scrub: dict, snapshots: dict) -> bool:
    """True when the scrub and the snapshot removal both completed."""
    return "remedy" not in scrub and not snapshots["failed"]


def finish(store, scrub) -> dict:
    """`mneme scrub`: remove this store's snapshots, scrub, clear the marker."""
    conn = store.conn
    snapshots = remove_snapshots(conn, db_path(conn))
    result = scrub(store)
    result["snapshots"] = snapshots
    if is_pending(conn):
        scanned = meta_get(conn, META_ERASE_PENDING) == SCANNED
        result["residual_scan"] = "ran_at_erase" if scanned else "not_run"
        result["residual_scan_note"] = SCAN_RAN if scanned else SCAN_NOT_RUN
        if finished(result, snapshots):
            clear_pending(conn)
            result["erase_pending"] = "cleared"
        else:
            result["erase_pending"] = "still_set"
    return result
