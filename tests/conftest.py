"""Shared test setup.

Replay snapshots live in a per-user state directory. Every test gets its own
snapshot root under pytest's temporary tree, so no test reads or writes the
real per-user directory, and a test that asks for `snapshot_root` can look at
exactly what Mneme left there. Python's temp directory is redirected too: an
erase and `mneme doctor` look there for snapshots older mneme versions left,
and a test must never open the owner's real files. For the same reason
MNEME_STATE is cleared, so an MCP test that forgets to bind its own state
cannot reach the owner's database.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


LEGACY_042_SQL = Path(__file__).resolve().parent / "fixtures" / "mneme-0.4.2.sql"


@pytest.fixture
def legacy_042_db(tmp_path) -> Path:
    """A database as released mneme 0.4.2 wrote it (tests/fixtures/build_mneme_042.py)."""
    import sqlite3

    db = tmp_path / "legacy-0.4.2.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(LEGACY_042_SQL.read_text(encoding="utf-8"))
    finally:
        conn.close()
    return db


@pytest.fixture(autouse=True)
def snapshot_root(tmp_path_factory, monkeypatch) -> Path:
    import mneme.snapshot_dir as snapshot_dir

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path_factory.mktemp("os-temp")))
    monkeypatch.delenv("MNEME_STATE", raising=False)
    root = tmp_path_factory.mktemp("state") / "mneme" / "snapshots"
    monkeypatch.setattr(snapshot_dir, "platform_snapshot_root", lambda: root)
    return root
