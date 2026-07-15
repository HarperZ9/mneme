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


class Store:
    """Thin, deterministic wrapper over a SQLite memory DB. A monotonic `ord`
    counter (persisted in meta) orders rows without a wall clock, so a rebuild
    from the same inputs is byte-identical."""

    SCHEMA_VERSION = "4"
    # columns added after the first published schema; an older DB is migrated
    # forward in place (ADD COLUMN is loss-free) instead of crashing on a raw
    # "no such column" error when a query reaches a newer field.
    _MIGRATIONS = (
        ("memories", "valid_until", "INTEGER"),
        ("memories", "superseded_by", "TEXT"),
        ("memories", '"user"', "TEXT NOT NULL DEFAULT ''"),
        ("memories", "source_hashes", "TEXT NOT NULL DEFAULT '{}'"),
        ("turns", "origin", "TEXT NOT NULL DEFAULT ''"),
    )

    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Bring an existing DB up to the current schema in place: add any
        column a newer version introduced, then stamp the schema version. A
        format change never surfaces as a raw sqlite traceback."""
        for table, column, decl in self._MIGRATIONS:
            cols = {r["name"] for r in
                    self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column.strip('"') not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        self._meta_set("schema_version", self.SCHEMA_VERSION)
        # anchor the audit head once, so tail truncation is detectable from here
        # on. A legacy log is anchored at its current tail (past truncation is
        # unrecoverable, but future truncation is caught).
        if self._meta_get("audit_count") is None:
            row = self.conn.execute(
                "SELECT COUNT(*) c, "
                "(SELECT entry_sha FROM audit ORDER BY ord DESC LIMIT 1) h "
                "FROM audit").fetchone()
            self._meta_set("audit_count", str(row["c"]))
            self._meta_set("audit_head", row["h"] or "")

    # -- meta (small key/value; ordinal + audit anchor + schema version) ------
    def _meta_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def _meta_set(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))

    # -- ordinal (clock-free ordering) ---------------------------------------
    def _next_ord(self) -> int:
        row = self.conn.execute("SELECT value FROM meta WHERE key='ord'").fetchone()
        n = int(row["value"]) + 1 if row else 0
        self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('ord',?)", (str(n),))
        return n

    # -- L0 turns ------------------------------------------------------------
    def add_turn(self, turn_id: str, session: str, role: str, text: str,
                 origin: dict | None = None) -> str:
        from .receipt import content_hash
        sha = content_hash(role, text)
        self.conn.execute(
            "INSERT OR REPLACE INTO turns(id,session,role,text,ord,content_sha256,origin) "
            "VALUES(?,?,?,?,?,?,?)",
            (turn_id, session, role, text, self._next_ord(), sha,
             json.dumps(origin) if origin else ""))
        self.conn.commit()
        return turn_id

    def turn_origin(self, turn_id: str) -> dict | None:
        """The external origin receipt bound to a turn (e.g. a gather source
        receipt), or None if the turn was not ingested from an external source."""
        row = self.turn(turn_id)
        if row is None or not row["origin"]:
            return None
        return json.loads(row["origin"])

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
                   session: str | None = None, user: str = "") -> ProvenanceReceipt:
        if layer not in LAYERS:
            raise ValueError(f"layer must be one of {LAYERS}, got {layer!r}")
        sids = list(source_ids)
        sha = memory_hash(text, sids, criterion)
        # id-collision guard: add_memory is idempotent for identical content in
        # the same partition, but it must never silently overwrite a row that
        # belongs to another user or carries different content (that is what the
        # audited update() path is for). Fail closed with a named error rather
        # than laundering a cross-tenant or unaudited rewrite through REPLACE.
        prior = self.memory(memory_id)
        if prior is not None:
            if prior["user"] != user:
                raise ValueError(
                    f"memory id {memory_id!r} already owned by user "
                    f"{prior['user']!r}; refusing cross-tenant overwrite")
            if prior["content_sha256"] != sha:
                raise ValueError(
                    f"memory id {memory_id!r} exists with different content; "
                    f"route a content change through update()")
        # snapshot each source's content hash AS IT IS NOW, so a later change to
        # a source (turn or cited memory) is detectable by re-comparison — the
        # content address binds source CONTENT, not just source ids.
        src_hashes = {}
        for sid in sids:
            src = self.turn(sid) or self.memory(sid)
            if src is not None:
                src_hashes[sid] = src["content_sha256"]
        self.conn.execute(
            "INSERT OR REPLACE INTO memories"
            '(id,layer,session,"user",text,source_ids,extractor,criterion,content_sha256,created_ord,source_hashes) '
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (memory_id, layer, session, user, text, json.dumps(sids), extractor,
             criterion, sha, self._next_ord(), json.dumps(src_hashes, sort_keys=True)))
        self.conn.commit()
        return ProvenanceReceipt(memory_id, layer, tuple(sids), extractor, criterion, sha)

    def memories(self, layer: str | None = None, session: str | None = None,
                 user: str | None = None, *, as_of: int | None = None,
                 include_superseded: bool = False) -> list[sqlite3.Row]:
        """Current memories by default (valid_until IS NULL). `as_of=N` returns
        the memories that were valid at ordinal N (temporal snapshot);
        `include_superseded` returns the full history including replaced ones."""
        q = "SELECT * FROM memories"
        conds, args = [], []
        if layer:
            conds.append("layer=?"); args.append(layer)
        if session:
            conds.append("session=?"); args.append(session)
        if user is not None:
            conds.append('"user"=?'); args.append(user)
        if as_of is not None:
            conds.append("created_ord<=?"); args.append(as_of)
            conds.append("(valid_until IS NULL OR valid_until>?)"); args.append(as_of)
        elif not include_superseded:
            conds.append("valid_until IS NULL")     # current memories only
        if conds:
            q += " WHERE " + " AND ".join(conds)
        return self.conn.execute(q + " ORDER BY created_ord", args).fetchall()

    def supersede(self, old_id: str, new_id: str, reason: str = "") -> dict | None:
        """Close a memory's validity (a fact CHANGED, not erased): mark it
        superseded by `new_id` as of now, KEEPING it for temporal history. Unlike
        forget (which erases the text for GDPR), supersede preserves the timeline.
        Returns the audit entry, or None if `old_id` is absent/already closed."""
        row = self.memory(old_id)
        if row is None or row["valid_until"] is not None:
            return None
        at = self._next_ord()
        self.conn.execute(
            "UPDATE memories SET valid_until=?, superseded_by=? WHERE id=?",
            (at, new_id, old_id))
        entry = self._audit("supersede", old_id, row["layer"],
                            row["content_sha256"], "", reason or f"superseded by {new_id}")
        self.conn.commit()
        return entry

    def users(self) -> list[str]:
        rows = self.conn.execute('SELECT DISTINCT "user" FROM memories ORDER BY "user"').fetchall()
        return [r["user"] for r in rows]

    def memory(self, memory_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()

    def provenance(self, memory_id: str) -> ProvenanceReceipt | None:
        r = self.memory(memory_id)
        if r is None:
            return None
        return ProvenanceReceipt(r["id"], r["layer"], tuple(json.loads(r["source_ids"])),
                                 r["extractor"], r["criterion"], r["content_sha256"])

    # -- accountable editing: forget / update leave a tombstone in an
    #    append-only, hash-chained audit log so the forgetting is itself auditable
    def _audit(self, op: str, memory_id: str, layer: str, before: str,
               after: str, reason: str) -> dict:
        from .receipt import content_hash
        prev = self.conn.execute(
            "SELECT entry_sha FROM audit ORDER BY ord DESC LIMIT 1").fetchone()
        prev_sha = prev["entry_sha"] if prev else ""
        # hash the fields as SEPARATE content_hash parts (each framed by \x1f), so
        # a field containing '|' cannot shift bytes across a boundary and forge a
        # colliding entry hash — a pre-joined "a|b" string could.
        entry = content_hash(prev_sha, op, memory_id, layer, before, after, reason)
        o = self._next_ord()
        self.conn.execute(
            "INSERT INTO audit(ord,op,memory_id,layer,before_sha,after_sha,reason,entry_sha) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (o, op, memory_id, layer, before, after, reason, entry))
        # advance the committed head anchor in the same transaction as the row:
        # verify_audit then rejects a truncated (or emptied) log, not just an
        # edited one.
        self._meta_set("audit_count", str(int(self._meta_get("audit_count") or "0") + 1))
        self._meta_set("audit_head", entry)
        self.conn.commit()
        return {"op": op, "memory_id": memory_id, "layer": layer,
                "before_sha": before, "after_sha": after, "reason": reason,
                "entry_sha": entry}

    def forget(self, memory_id: str, reason: str = "") -> dict | None:
        """Delete a memory, leaving a tombstone receipt (what was forgotten, its
        hash, why). Returns the audit entry, or None if the memory is absent."""
        row = self.memory(memory_id)
        if row is None:
            return None
        entry = self._audit("forget", memory_id, row["layer"],
                            row["content_sha256"], "", reason)
        self.conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        self.conn.commit()
        return entry

    def update(self, memory_id: str, new_text: str, reason: str = "") -> dict | None:
        """Replace a memory's text, re-deriving its hash and leaving an audit
        entry (before/after hash, why). Provenance (sources, criterion) is kept."""
        from .receipt import memory_hash
        row = self.memory(memory_id)
        if row is None:
            return None
        source_ids = json.loads(row["source_ids"])
        after = memory_hash(new_text, source_ids, row["criterion"])
        entry = self._audit("update", memory_id, row["layer"],
                            row["content_sha256"], after, reason)
        self.conn.execute(
            "UPDATE memories SET text=?, content_sha256=? WHERE id=?",
            (new_text, after, memory_id))
        self.conn.commit()
        return entry

    def audit_log(self) -> list:
        return self.conn.execute("SELECT * FROM audit ORDER BY ord").fetchall()

    def verify_audit(self) -> bool:
        """Re-derive the audit chain; True iff every entry hash reproduces AND
        the chain still ends at the committed head (count + last entry_sha). A
        deleted, reordered, edited, OR truncated tombstone breaks it — you cannot
        quietly forget that you forgot something, and you cannot forget that you
        forgot by lopping off the tail."""
        from .receipt import content_hash
        prev = ""
        count = 0
        for e in self.audit_log():
            prev = content_hash(prev, e["op"], e["memory_id"], e["layer"],
                                e["before_sha"], e["after_sha"], e["reason"])
            if prev != e["entry_sha"]:
                return False
            count += 1
        head = self._meta_get("audit_head")
        expected = self._meta_get("audit_count")
        if head is None or expected is None:
            return True                 # unanchored legacy log: chain-only check
        return prev == head and count == int(expected)

    def close(self) -> None:
        self.conn.close()
