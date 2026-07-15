"""Falsifiers for explicit union views + in-place schema migration.

Load-bearing:
  1. history(user=None) over a multi-tenant store must not present one tenant's
     fact as THE 'current' single-subject answer — it withholds the scalar;
  2. entity_graph(user=None) must attribute relations per tenant, not collapse
     two people into one contradictory 'user' node;
  3. a DB created under an older schema (missing a newer column) is migrated in
     place on open, not crashed with a raw sqlite error.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory
from mneme.store import Store


def _two_tenants():
    m = AgentMemory(":memory:")
    m.remember("chat1", [{"role": "user", "text": "I live in Denver."}], user="alice")
    m.remember("chat2", [{"role": "user", "text": "I live in Boston."}], user="bob")
    return m


def test_history_withholds_scalar_current_across_tenants():
    m = _two_tenants()
    hist = m.history(predicate="lives_in")            # user=None -> spans alice+bob
    assert hist["spans_tenants"] is True
    assert hist["current"] is None                    # not "I live in Boston."
    assert hist["transitions"] is None
    # each timeline row is attributed to its owner
    assert {t["user"] for t in hist["timeline"]} == {"alice", "bob"}
    # scoping to one user restores the scalar
    scoped = m.history(predicate="lives_in", user="alice")
    assert scoped["spans_tenants"] is False
    assert scoped["current"] == "I live in Denver."


def test_entity_graph_attributes_relations_per_tenant():
    m = _two_tenants()
    g = m.entity_graph()                              # user=None
    lives = {(e["from"], e["to"]) for e in g["edges"] if e["predicate"] == "lives_in"}
    assert ("alice", "denver") in lives
    assert ("bob", "boston") in lives
    # the two subjects are distinct nodes, not one merged "user"
    subjects = {n["id"] for n in g["nodes"] if n["kind"] == "subject"}
    assert {"alice", "bob"} <= subjects
    assert "user" not in subjects


def test_old_db_missing_a_column_is_migrated_not_crashed(tmp_path):
    db = tmp_path / "old.db"
    # hand-build a pre-temporal memories table (no valid_until/superseded_by/
    # source_hashes) the way an older mneme would have
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE memories (id TEXT PRIMARY KEY, layer TEXT NOT NULL, "
        "session TEXT, \"user\" TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, "
        "source_ids TEXT NOT NULL, extractor TEXT NOT NULL, criterion TEXT NOT NULL, "
        "content_sha256 TEXT NOT NULL, created_ord INTEGER NOT NULL);"
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
    conn.commit()
    conn.close()
    # opening through Store migrates the schema forward in place
    s = Store(str(db))
    cols = {r["name"] for r in s.conn.execute("PRAGMA table_info(memories)").fetchall()}
    assert {"valid_until", "superseded_by", "source_hashes"} <= cols
    assert s._meta_get("schema_version") == Store.SCHEMA_VERSION
    # and a default-path query that references a newer column no longer crashes
    assert s.memories(layer="L1") == []
    s.close()
