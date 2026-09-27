"""Falsifiers for the schema high-water mark.

An older mneme stamps its own `schema_version` on every open. Without a mark
that only moves up, a newer mneme cannot tell that an older one wrote to the
database in between, and the erase guarantees for rows written then may not
hold. The warning is also kept in the database, so a later status call can
show it after the terminal that printed it is gone.
"""
from __future__ import annotations

import sqlite3
import warnings

import pytest

from mneme import AgentMemory
from mneme.schema import SCHEMA_VERSION
from mneme.schema_guard import SchemaDowngradeWarning


def _meta(db, key):
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _set_meta(db, key, value):
    conn = sqlite3.connect(db)
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
    conn.commit()
    conn.close()


def test_a_new_database_records_its_high_water_mark(tmp_path):
    db = tmp_path / "mneme.db"
    AgentMemory(db).close()

    assert _meta(db, "schema_version") == SCHEMA_VERSION
    assert _meta(db, "schema_high_water") == SCHEMA_VERSION


def test_a_version_set_back_by_an_older_mneme_warns_and_remigrates(tmp_path):
    db = tmp_path / "mneme.db"
    AgentMemory(db).close()
    older = str(int(SCHEMA_VERSION) - 1)
    _set_meta(db, "schema_version", older)          # what an older mneme's open does

    with pytest.warns(SchemaDowngradeWarning, match="an older mneme rewrote"):
        memory = AgentMemory(db)

    assert memory.store.schema_warning is not None
    memory.close()
    assert _meta(db, "schema_version") == SCHEMA_VERSION
    assert _meta(db, "schema_downgrade_seen") == older


def test_the_high_water_mark_never_moves_down(tmp_path):
    db = tmp_path / "mneme.db"
    AgentMemory(db).close()
    newer = str(int(SCHEMA_VERSION) + 1)
    _set_meta(db, "schema_high_water", newer)       # written by a newer mneme

    with pytest.warns(SchemaDowngradeWarning, match="newer mneme"):
        AgentMemory(db).close()

    assert _meta(db, "schema_high_water") == newer
    assert _meta(db, "schema_version") == SCHEMA_VERSION


def test_an_ordinary_reopen_is_silent(tmp_path):
    db = tmp_path / "mneme.db"
    AgentMemory(db).close()

    with warnings.catch_warnings():
        warnings.simplefilter("error", SchemaDowngradeWarning)
        memory = AgentMemory(db)
    assert memory.store.schema_warning is None
    memory.close()


def test_a_legacy_database_without_a_mark_is_adopted_without_warning(tmp_path):
    db = tmp_path / "legacy.db"
    AgentMemory(db).close()
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM meta WHERE key IN ('schema_high_water', 'store_id')")
    conn.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
    conn.commit()
    conn.close()

    with warnings.catch_warnings():
        warnings.simplefilter("error", SchemaDowngradeWarning)
        AgentMemory(db).close()

    assert _meta(db, "schema_high_water") == SCHEMA_VERSION
    assert _meta(db, "store_id") is not None
