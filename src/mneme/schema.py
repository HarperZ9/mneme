"""schema.py — the SQLite schema and its forward migrations, kept beside the
store so the DDL and the version history read as one thing.

SCHEMA is applied via CREATE TABLE IF NOT EXISTS (a no-op on an existing table);
MIGRATIONS adds any column a newer version introduced to an older DB in place,
so a format change never surfaces as a raw sqlite traceback.
"""
from __future__ import annotations

SCHEMA_VERSION = "4"

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY, session TEXT NOT NULL, role TEXT NOT NULL,
    text TEXT NOT NULL, ord INTEGER NOT NULL, content_sha256 TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY, layer TEXT NOT NULL, session TEXT, "user" TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL, source_ids TEXT NOT NULL, extractor TEXT NOT NULL,
    criterion TEXT NOT NULL, content_sha256 TEXT NOT NULL, created_ord INTEGER NOT NULL,
    valid_until INTEGER, superseded_by TEXT,
    source_hashes TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_mem_layer ON memories(layer);
CREATE INDEX IF NOT EXISTS idx_mem_session ON memories(session);
CREATE INDEX IF NOT EXISTS idx_mem_user ON memories("user");
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
    ord INTEGER PRIMARY KEY, op TEXT NOT NULL, memory_id TEXT NOT NULL,
    layer TEXT NOT NULL, before_sha TEXT NOT NULL, after_sha TEXT NOT NULL,
    reason TEXT NOT NULL, entry_sha TEXT NOT NULL
);
"""

# (table, column, decl) added after the first published schema; applied only
# when the column is missing, so it is loss-free and idempotent.
MIGRATIONS = (
    ("memories", "valid_until", "INTEGER"),
    ("memories", "superseded_by", "TEXT"),
    ("memories", '"user"', "TEXT NOT NULL DEFAULT ''"),
    ("memories", "source_hashes", "TEXT NOT NULL DEFAULT '{}'"),
    ("turns", "origin", "TEXT NOT NULL DEFAULT ''"),
)
