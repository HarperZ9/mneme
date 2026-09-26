"""Falsifiers against a database that released mneme 0.4.2 wrote.

The fixture (tests/fixtures/mneme-0.4.2.sql) holds an unsalted update, an
unsalted supersede and a 0.4.2 row-level forget whose raw turn t3 stayed in
the store. Opening it must migrate without a warning, status and doctor must
count what the old forget left, and `forget --turn` must reach that turn.
"""
from __future__ import annotations

import json
import sqlite3
import warnings

from mneme import AgentMemory
from mneme.cli import main
from mneme.state_report import describe


def test_the_042_database_migrates_to_schema_5_without_a_warning(legacy_042_db):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        memory = AgentMemory(legacy_042_db)

    assert memory.store._meta_get("schema_version") == "5"
    assert memory.store._meta_get("schema_high_water") == "5"
    assert memory.audit()["chain_intact"] is True
    memory.close()
    tables = {r[0] for r in sqlite3.connect(legacy_042_db).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"salts", "merges"} <= tables


def test_its_earlier_update_and_supersede_rows_are_counted_as_unsalted(legacy_042_db):
    memory = AgentMemory(legacy_042_db)
    denver = next(r["id"] for r in memory.store.memories() if "Denver" in r["text"])
    memory.update(denver, "I live in Denver, Colorado.", reason="precise")

    receipt = memory.forget(denver, reason="user asked", allow_collateral=True)

    legacy = receipt["residue"]["legacy_audit_rows"]
    assert legacy == {**legacy, "count": 2, "unsalted_hashes": 1, "blinded": 1}
    memory.close()


def test_status_counts_what_a_042_forget_left_behind(legacy_042_db):
    report = describe(str(legacy_042_db))

    assert report["legacy"]["unsalted_forget_rows"] == 1
    assert report["legacy"]["uncited_turns"] == 1
    assert any("--turn" in note for note in report["notes"])


def test_forget_by_turn_reaches_the_turn_a_042_forget_left(legacy_042_db, capsys):
    rc = main(["--state", str(legacy_042_db), "forget", "t3", "--turn", "--yes",
               "--reason", "left by an old forget"])

    receipt = json.loads(capsys.readouterr().out)
    assert rc == 0 and receipt["counts"]["turns"] == 1
    assert b"night nurse" not in legacy_042_db.read_bytes()
