"""schema_guard.py: a schema high-water mark, so version mixing is visible.

Every mneme stamps its own `schema_version` when it opens a database it can
write, and mneme 0.4.2 and earlier do so unconditionally. On its own that
hides a downgrade: after a newer mneme writes the database and an older one
reopens it, the stored version looks current again, and the older mneme keeps
writing rows without the newer guarantees (for example unblinded audit hashes
or a forget that leaves raw turns).

`schema_high_water` only moves up. On open, a stored version below the mark
means an older mneme reopened the database since the mark was set; a running
version below the mark means this mneme is the older one. Either case warns,
and the first case is also kept in meta (`schema_downgrade_seen`) so status
and doctor can report it after the warning has scrolled away. The migrations
then run again, which re-creates anything the older version lacked.
"""
from __future__ import annotations

import sqlite3
import warnings

from .audit_writer import meta_get, meta_set
from .schema import META_SCHEMA_DOWNGRADE_SEEN, META_SCHEMA_HIGH_WATER


class SchemaDowngradeWarning(UserWarning):
    """This database was opened by mneme versions with different guarantees."""


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def read(conn: sqlite3.Connection) -> dict:
    """The stored version, high-water mark and downgrade marker, as ints or None."""
    return {"stored": _as_int(meta_get(conn, "schema_version")),
            "high_water": _as_int(meta_get(conn, META_SCHEMA_HIGH_WATER)),
            "downgrade_seen": _as_int(meta_get(conn, META_SCHEMA_DOWNGRADE_SEEN))}


def findings(stored: int | None, high: int | None, running: int,
             downgrade_seen: int | None = None) -> list[str]:
    """Version-mixing findings for a database, worded for the owner."""
    problems = []
    if high is not None and stored is not None and stored < high:
        problems.append(
            f"an older mneme rewrote this database (schema_version {stored} is "
            f"below the high-water mark {high}); erase guarantees for rows "
            "written since then may not hold")
    elif downgrade_seen is not None:
        problems.append(
            f"an older mneme reopened this database earlier (it stamped schema "
            f"{downgrade_seen}); erase guarantees for rows it wrote may not hold")
    known = max(v for v in (high, stored, running) if v is not None)
    if running < known:
        problems.append(
            f"this database was written by a newer mneme (schema {known}); this "
            f"mneme (schema {running}) may not keep that version's guarantees")
    return problems


def stamp(conn: sqlite3.Connection, current: str) -> str | None:
    """Stamp `current`, raise the high-water mark, and warn on version mixing.

    Returns the warning text, or None. Does not commit; the store's open
    commits it together with the migrations.
    """
    running = int(current)
    state = read(conn)
    stored, high = state["stored"], state["high_water"]
    problems = findings(stored, high, running)
    if high is not None and stored is not None and stored < high:
        meta_set(conn, META_SCHEMA_DOWNGRADE_SEEN, str(stored))
    known = max(v for v in (high, stored, running) if v is not None)
    meta_set(conn, "schema_version", current)
    meta_set(conn, META_SCHEMA_HIGH_WATER, str(known))
    if not problems:
        return None
    message = "; ".join(problems)
    warnings.warn(SchemaDowngradeWarning(message), stacklevel=2)
    return message
