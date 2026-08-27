"""End-to-end check on the vendored standalone verifier at the repo root.

verify_audit.py must re-derive the audit hash chain from the raw .db bytes using
only the standard library, with no mneme import. A stranger runs it against a
store file and gets exit 0 on a clean chain, exit 1 on a tampered row, exit 2
when the artifact is missing.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory

VERIFIER = Path(__file__).resolve().parents[1] / "verify_audit.py"
TURNS = [
    {"id": "t1", "role": "user", "text": "My name is Dana and I live in Denver."},
    {"id": "t2", "role": "user", "text": "I prefer dark roast coffee."},
]


def _sealed_db(path: Path) -> None:
    m = AgentMemory(str(path))
    m.remember("s", TURNS)
    ids = [r["id"] for r in m.store.memories(layer="L1")]
    m.forget(ids[0], reason="one|x")
    m.update(ids[1], "two", reason="two")
    assert m.store.verify_audit() is True
    m.close()


def _run(db: Path) -> int:
    return subprocess.run([sys.executable, str(VERIFIER), str(db)]).returncode


def test_clean_chain_verifies(tmp_path):
    db = tmp_path / "clean.db"
    _sealed_db(db)
    assert _run(db) == 0


def test_tampered_row_is_drift(tmp_path):
    import sqlite3
    db = tmp_path / "tampered.db"
    _sealed_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE audit SET entry_sha='0'||substr(entry_sha,2) "
        "WHERE ord=(SELECT MIN(ord) FROM audit)")
    conn.commit()
    conn.close()
    assert _run(db) == 1


def test_missing_artifact_is_unverifiable(tmp_path):
    assert _run(tmp_path / "does_not_exist.db") == 2
