"""Falsifiers for blinded audit history (schema 5).

Before schema 5, `update` wrote the content hash of the old and the new version
into the audit log, and `supersede` wrote the hash of the closed version. The
log is append-only, so those hashes outlived an erase and could confirm a guess
of what was erased. From schema 5 each such value is a salted commitment
`b1:` + sha256(tag || 0x00 || salt || content_sha256). The salt sits in the
`salts` table under the memory it describes, and an erase deletes it in the
same transaction as the rows, after which the commitment links to nothing.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from mneme import AgentMemory, audit_blind, audit_writer
from mneme.receipt import content_hash
from mneme.schema import SCHEMA_VERSION

TURNS = [
    {"id": "t1", "role": "user", "text": "I live in Denver."},
    {"id": "t2", "role": "user", "text": "I prefer dark roast coffee."},
]
BLINDED = re.compile(r"b1:[0-9a-f]{64}")
VERIFIER = Path(__file__).resolve().parents[1] / "verify_audit.py"


def _memory(path=":memory:"):
    memory = AgentMemory(path)
    memory.remember("s", TURNS)
    return memory


def _atom(memory, needle: str) -> str:
    return next(r["id"] for r in memory.store.memories(layer="L1", include_superseded=True)
                if needle in r["text"])


def _digests(*texts_and_shas: str) -> set[str]:
    """Every unsalted value that could confirm a guess: the stored content
    hashes and the plain digests of each text, plus their 12-character prefixes."""
    out = set()
    for value in texts_and_shas:
        out |= {value, content_hash(value), hashlib.sha256(value.encode()).hexdigest()}
    return out | {v[:12] for v in out if re.fullmatch(r"[0-9a-f]{12,}", v)}


def _history(memory):
    """Update then supersede one atom; return the atom, its successor, and every
    version's text and content hash."""
    atom = _atom(memory, "Denver")
    first = memory.store.memory(atom)
    memory.update(atom, "I live in Denver, Colorado.", reason="more precise")
    second = memory.store.memory(atom)
    successor = memory.supersede(atom, "I live in Seattle.", reason="moved")["new_id"]
    third = memory.store.memory(successor)
    versions = [(r["text"], r["content_sha256"]) for r in (first, second, third)]
    return atom, successor, versions


def _history_rows(memory):
    return [r for r in memory.store.audit_log() if r["op"] in ("update", "supersede")]


def test_update_and_supersede_rows_hold_no_unsalted_hash_of_either_version():
    memory = _memory()
    _atom_id, _successor, versions = _history(memory)
    forbidden = _digests(*[v for pair in versions for v in pair])

    rows = _history_rows(memory)

    assert [r["op"] for r in rows] == ["update", "supersede"]
    for row in rows:
        values = [row["before_sha"], row["after_sha"]]
        assert all(BLINDED.fullmatch(v) for v in values if v)
        stored = "|".join((row["before_sha"], row["after_sha"], row["reason"]))
        assert not [d for d in forbidden if d in stored]
    assert rows[1]["after_sha"] == ""              # supersede closes; no after value
    assert memory.audit()["chain_intact"] is True


def test_each_blinded_value_opens_to_its_version_and_to_nothing_else():
    memory = _memory()
    _atom_id, _successor, versions = _history(memory)
    update, supersede = _history_rows(memory)
    (_t1, sha1), (_t2, sha2), (_t3, _sha3) = versions
    conn = memory.store.conn

    assert audit_blind.opens(conn, update["before_sha"], sha1)
    assert audit_blind.opens(conn, update["after_sha"], sha2)
    assert audit_blind.opens(conn, supersede["before_sha"], sha2)
    assert not audit_blind.opens(conn, update["before_sha"], sha2)
    assert not audit_blind.opens(conn, update["after_sha"], sha1)
    assert not audit_blind.opens(conn, "b1:" + "0" * 64, sha1)


def test_two_updates_to_the_same_text_do_not_share_a_value():
    memory = _memory()
    atom = _atom(memory, "Denver")
    memory.update(atom, "I live in Boulder.", reason="a")
    memory.update(atom, "I live in Denver.", reason="b")
    memory.update(atom, "I live in Boulder.", reason="c")

    afters = [r["after_sha"] for r in memory.store.audit_log() if r["op"] == "update"]

    assert afters[0] != afters[2]                  # same content, fresh salt each time


def test_erase_deletes_the_salts_so_the_history_links_to_nothing(tmp_path):
    db = tmp_path / "mneme.db"
    memory = _memory(db)
    atom, successor, versions = _history(memory)
    rows = _history_rows(memory)
    salts = [r[0] for r in memory.store.conn.execute("SELECT salt FROM salts")]
    assert len(salts) == 3

    receipt = memory.forget(atom, reason="user asked", allow_collateral=True)

    conn = memory.store.conn
    assert conn.execute("SELECT COUNT(*) FROM salts").fetchone()[0] == 0
    assert receipt["structural"]["salts_deleted"] == 3
    assert receipt["structural"]["rows_absent"] is True
    assert not audit_blind.opens(conn, rows[0]["before_sha"], versions[0][1])
    legacy = receipt["residue"]["legacy_audit_rows"]
    assert legacy["count"] == 2 and legacy["blinded"] == 2
    assert legacy["unsalted_hashes"] == 0
    assert memory.audit()["chain_intact"] is True
    memory.close()
    data = db.read_bytes()
    assert not [s for s in salts if s.encode("ascii") in data or bytes.fromhex(s) in data]
    assert successor.encode("ascii") not in data
    proc = subprocess.run([sys.executable, str(VERIFIER), str(db)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and proc.stdout.startswith("MATCH"), proc.stdout


def test_erase_of_one_memory_keeps_the_salts_of_another():
    memory = _memory()
    coffee = _atom(memory, "coffee")
    memory.update(coffee, "I prefer light roast coffee.", reason="changed")
    denver = _atom(memory, "Denver")
    memory.update(denver, "I live in Denver, Colorado.", reason="precise")

    memory.forget(denver, reason="user asked")

    subjects = {r[0] for r in memory.store.conn.execute("SELECT subject_id FROM salts")}
    assert subjects == {coffee}


def test_the_row_level_forget_blinds_its_tombstone_and_drops_the_row_salts():
    memory = _memory()
    atom = _atom(memory, "Denver")
    memory.update(atom, "I live in Denver, Colorado.", reason="precise")
    sha = memory.store.memory(atom)["content_sha256"]

    entry = memory.store.forget(atom, reason="merged into a duplicate")

    assert BLINDED.fullmatch(entry["before_sha"]) and entry["after_sha"] == ""
    assert sha not in entry["before_sha"]
    conn = memory.store.conn
    assert conn.execute("SELECT COUNT(*) FROM salts").fetchone()[0] == 0
    assert not audit_blind.opens(conn, entry["before_sha"], sha)
    assert memory.audit()["chain_intact"] is True


def test_a_failure_before_commit_leaves_no_salt_row_change_or_audit_entry(monkeypatch):
    memory = _memory()
    atom = _atom(memory, "Denver")
    text = memory.store.memory(atom)["text"]
    entries = len(memory.store.audit_log())

    def crash(*_args, **_kwargs):
        raise RuntimeError("injected crash")

    monkeypatch.setattr(audit_writer, "append", crash)
    with pytest.raises(RuntimeError, match="injected"):
        memory.update(atom, "I live in Boulder.", reason="moved")
    monkeypatch.undo()
    memory.store.conn.commit()                     # a later commit must not land it

    assert memory.store.memory(atom)["text"] == text
    assert memory.store.conn.execute("SELECT COUNT(*) FROM salts").fetchone()[0] == 0
    assert len(memory.store.audit_log()) == entries


def _schema_4_database(db) -> str:
    """A database as mneme at schema 4 left it: one unsalted update row, no salts table."""
    memory = _memory(db)
    atom = _atom(memory, "Denver")
    row = memory.store.memory(atom)
    conn = memory.store.conn
    new_sha = "e" * 64
    conn.execute("UPDATE memories SET text=?, content_sha256=? WHERE id=?",
                 ("I live in Denver, CO.", new_sha, atom))
    audit_writer.append(conn, "update", atom, "L1", row["content_sha256"], new_sha, "old")
    conn.execute("DROP TABLE salts")
    for key in ("schema_version", "schema_high_water"):
        conn.execute("UPDATE meta SET value='4' WHERE key=?", (key,))
    conn.commit()
    memory.close()
    return atom


def test_a_schema_4_database_migrates_and_its_earlier_rows_are_counted(tmp_path):
    db = tmp_path / "legacy.db"
    atom = _schema_4_database(db)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        memory = AgentMemory(db)
    assert SCHEMA_VERSION == "5"
    assert memory.store._meta_get("schema_version") == "5"
    assert memory.store._meta_get("schema_high_water") == "5"
    memory.update(atom, "I live in Denver, Colorado.", reason="precise")
    receipt = memory.forget(atom, reason="user asked")

    legacy = receipt["residue"]["legacy_audit_rows"]
    assert legacy == {**legacy, "count": 2, "unsalted_hashes": 1, "blinded": 1}
    assert memory.audit()["chain_intact"] is True
    memory.close()
    tables = {r[0] for r in sqlite3.connect(db).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "salts" in tables
