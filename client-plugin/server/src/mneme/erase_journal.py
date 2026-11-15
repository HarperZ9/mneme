"""erase_journal.py: overwrite the rollback journal of an erase.

In a rollback-journal mode SQLite copies each page an erase is about to change
into `<database>-journal`, so that file holds the erased text, and at commit
SQLite unlinks it without overwriting it. `begin` therefore takes the
connection into exclusive locking with a persistent journal for the length of
the erase transaction: the journal stays after the commit, and no other
connection can start one while this one holds the lock. `finish` writes zeros
over the journal (wipe.py), puts the journal and locking modes back, which
removes the file in delete mode, and reads once so SQLite releases the lock.

A write-ahead log is different: other connections read and append to it, and
there is no point at which this process alone owns its bytes, so mneme
checkpoints and truncates it (erase_scrub.py) and does not overwrite it. An
in-memory store has no journal file. The receipt says which case applied.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from . import wipe

ROLLBACK_MODES = ("delete", "truncate", "persist")
_LOG = logging.getLogger("mneme")


def _pragma(conn: sqlite3.Connection, text: str) -> str:
    row = conn.execute(f"PRAGMA {text}").fetchone()
    return str(row[0]).lower() if row else ""


def _main_file(conn: sqlite3.Connection) -> str:
    return next((row[2] for row in conn.execute("PRAGMA database_list")
                 if row[1] == "main"), "")


def begin(conn: sqlite3.Connection) -> dict | None:
    """Hold the journal for this erase. None when there is no journal file to hold."""
    main, mode = _main_file(conn), _pragma(conn, "journal_mode")
    if not main or mode not in ROLLBACK_MODES:
        return None
    state = {"path": Path(f"{main}-journal"), "mode": mode,
             "locking": _pragma(conn, "locking_mode")}
    _pragma(conn, "locking_mode=EXCLUSIVE")
    try:
        held = _pragma(conn, "journal_mode=PERSIST") == "persist"
    except sqlite3.Error:
        restore(conn, state)          # a locked database: the erase is refused as before
        raise
    if not held:
        restore(conn, state)
        return None
    return state


def _overwrite(path: Path) -> int:
    # a seam: tests read the journal here, before and after the zeros go in
    return wipe.overwrite(path)


def restore(conn: sqlite3.Connection, state: dict | None) -> None:
    """Put the journal and locking modes back and release the lock."""
    if state is None:
        return
    try:
        _pragma(conn, f"locking_mode={state['locking']}")
        _pragma(conn, f"journal_mode={state['mode']}")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error as exc:      # the modes are set; only the release read failed
        _LOG.warning("mneme: could not read after restoring the journal mode: %s",
                     exc.__class__.__name__)


def finish(conn: sqlite3.Connection, state: dict | None, mode: str) -> str:
    """Overwrite the journal after the commit. Returns what happened, for the receipt."""
    if state is None:
        return "not_overwritten_wal" if mode == "wal" else "no_journal_file"
    try:
        _overwrite(state["path"])
        result = "overwritten"
    except FileNotFoundError:
        result = "absent"
    except OSError as exc:
        _LOG.warning("mneme: could not overwrite the rollback journal: %s",
                     exc.__class__.__name__)
        result = "overwrite_failed"
    finally:
        restore(conn, state)
    return result
