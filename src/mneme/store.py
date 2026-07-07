"""store.py — the SQLite substrate for the 4-tier memory (stdlib sqlite3, zero-dep).

Layers, matching the class leader's L0-L3 so the feature surface is on par:
  L0 turn      raw dialogue turns (role, text, session)
  L1 atom      atomic facts extracted from turns
  L2 scenario  scene blocks grouping related atoms
  L3 persona   the user profile synthesized from scenarios

Every memory row carries its provenance (source_ids, extractor, criterion,
content_sha256) so a recall or a drift check re-derives from the same bytes.
The store is pure storage: extraction (extract.py), retrieval (recall.py), and
drift (drift.py) are separate organs that read/write through it.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .receipt import ProvenanceReceipt, memory_hash

LAYERS = ("L0", "L1", "L2", "L3")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY, session TEXT NOT NULL, role TEXT NOT NULL,
    text TEXT NOT NULL, ord INTEGER NOT NULL, content_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY, layer TEXT NOT NULL, session TEXT,
    text TEXT NOT NULL, source_ids TEXT NOT NULL, extractor TEXT NOT NULL,
    criterion TEXT NOT NULL, content_sha256 TEXT NOT NULL, created_ord INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_layer ON memories(layer);
CREATE INDEX IF NOT EXISTS idx_mem_session ON memories(session);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Store:
    """Thin, deterministic wrapper over a SQLite memory DB. A monotonic `ord`
    counter (persisted in meta) orders rows without a wall clock, so a rebuild
    from the same inputs is byte-identical."""

    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # -- ordinal (clock-free ordering) ---------------------------------------
    def _next_ord(self) -> int:
        row = self.conn.execute("SELECT value FROM meta WHERE key='ord'").fetchone()
        n = int(row["value"]) + 1 if row else 0
        self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('ord',?)", (str(n),))
        return n

    # -- L0 turns ------------------------------------------------------------
    def add_turn(self, turn_id: str, session: str, role: str, text: str) -> str:
        from .receipt import content_hash
        sha = content_hash(role, text)
        self.conn.execute(
            "INSERT OR REPLACE INTO turns(id,session,role,text,ord,content_sha256) "
            "VALUES(?,?,?,?,?,?)",
            (turn_id, session, role, text, self._next_ord(), sha))
        self.conn.commit()
        return turn_id

    def turns(self, session: str | None = None) -> list[sqlite3.Row]:
        if session is None:
            return self.conn.execute("SELECT * FROM turns ORDER BY ord").fetchall()
        return self.conn.execute(
            "SELECT * FROM turns WHERE session=? ORDER BY ord", (session,)).fetchall()

    def turn(self, turn_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()

    # -- memories (L1-L3) ----------------------------------------------------
    def add_memory(self, memory_id: str, layer: str, text: str,
                   source_ids: Iterable[str], extractor: str, criterion: str,
                   session: str | None = None) -> ProvenanceReceipt:
        if layer not in LAYERS:
            raise ValueError(f"layer must be one of {LAYERS}, got {layer!r}")
        sids = list(source_ids)
        sha = memory_hash(text, sids, criterion)
        self.conn.execute(
            "INSERT OR REPLACE INTO memories"
            "(id,layer,session,text,source_ids,extractor,criterion,content_sha256,created_ord) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (memory_id, layer, session, text, json.dumps(sids), extractor,
             criterion, sha, self._next_ord()))
        self.conn.commit()
        return ProvenanceReceipt(memory_id, layer, tuple(sids), extractor, criterion, sha)

    def memories(self, layer: str | None = None,
                 session: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM memories"
        conds, args = [], []
        if layer:
            conds.append("layer=?"); args.append(layer)
        if session:
            conds.append("session=?"); args.append(session)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        return self.conn.execute(q + " ORDER BY created_ord", args).fetchall()

    def memory(self, memory_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()

    def provenance(self, memory_id: str) -> ProvenanceReceipt | None:
        r = self.memory(memory_id)
        if r is None:
            return None
        return ProvenanceReceipt(r["id"], r["layer"], tuple(json.loads(r["source_ids"])),
                                 r["extractor"], r["criterion"], r["content_sha256"])

    def close(self) -> None:
        self.conn.close()
