"""schema.py — the SQLite schema and its forward migrations, kept beside the
store so the DDL and the version history read as one thing.

SCHEMA is applied via CREATE TABLE IF NOT EXISTS (a no-op on an existing table);
MIGRATIONS adds any column a newer version introduced to an older DB in place,
so a format change never surfaces as a raw sqlite traceback.
"""
from __future__ import annotations

SCHEMA_VERSION = "5"

# meta keys. store_id is a random id that names this store's replay snapshot
# directory; the high-water mark only moves up, so a newer mneme can tell that
# an older one reopened the database and stamped its own, lower version.
META_STORE_ID = "store_id"
META_SCHEMA_HIGH_WATER = "schema_high_water"
META_SCHEMA_DOWNGRADE_SEEN = "schema_downgrade_seen"
# set in an erase's transaction and cleared once its scrub and receipt finish;
# while it is set, status and doctor tell the owner to run `mneme scrub`
META_ERASE_PENDING = "erase_pending"

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
-- schema 5: the salts of the blinded update, supersede and forget values in
-- the audit log (audit_blind.py), kept under the memory each one describes.
-- An erase deletes a memory's salts, after which those values open to nothing.
CREATE TABLE IF NOT EXISTS salts (
    value TEXT PRIMARY KEY, subject_id TEXT NOT NULL, salt TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_salts_subject ON salts(subject_id);
-- schema 5: consolidation's merge links. A near-duplicate merged away loses its
-- row, but its source turns stay, so an erase of the kept memory reads them here.
CREATE TABLE IF NOT EXISTS merges (
    dropped_id TEXT PRIMARY KEY, kept_id TEXT NOT NULL, source_ids TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_merges_kept ON merges(kept_id);
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
