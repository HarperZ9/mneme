"""Falsifiers for receipt facts that were fixed text or never exercised.

The freed-clusters note named a rollback journal on a WAL store and claimed a
shrunk file after a failed VACUUM. It is now built from what the scrub did.
`rows_absent` is a defensive check no other test reached. A database an older
mneme reopened is now named in the receipt, not only by `mneme doctor`. Every
text is planted test data.
"""
from __future__ import annotations

import pytest

from mneme import AgentMemory
from mneme.audit_writer import meta_set
from mneme.erase_receipt import freed_clusters_note, rows_absent
from mneme.schema import META_SCHEMA_DOWNGRADE_SEEN

TURNS = [{"id": "t1", "role": "user", "text": "I live in Denver near the park."},
         {"id": "t2", "role": "user", "text": "I prefer green tea daily."}]


def _memory(tmp_path, *, wal: bool = False):
    memory = AgentMemory(tmp_path / "mneme.db")
    if wal:
        memory.store.conn.execute("PRAGMA journal_mode=WAL")
    memory.remember("s", TURNS)
    atom = next(r["id"] for r in memory.store.memories(layer="L1") if "Denver" in r["text"])
    return memory, atom


@pytest.mark.parametrize("wal, journal_word", [(True, "write-ahead log"),
                                                (False, "rollback journal")])
def test_the_freed_clusters_note_names_the_journal_this_store_uses(tmp_path, wal,
                                                                   journal_word):
    memory, atom = _memory(tmp_path, wal=wal)

    receipt = memory.forget(atom, reason="user asked")
    memory.close()

    note = receipt["residue"]["freed_clusters"]["note"]
    assert journal_word in note
    assert "shrank" in note


def test_the_freed_clusters_note_does_not_claim_a_vacuum_that_failed():
    note = freed_clusters_note({"vacuum": "failed: planted", "journal_mode": "delete"})

    assert "shrank" not in note
    assert "did not" in note


def test_rows_absent_is_false_while_a_planned_row_remains():
    memory = AgentMemory(":memory:")
    memory.remember("s", TURNS)
    plan = {"memories": [], "turns": ["t1"]}

    assert rows_absent(memory.store.conn, plan) is False
    assert rows_absent(memory.store.conn, {"memories": [], "turns": ["gone"]}) is True


def test_the_receipt_names_an_earlier_version_downgrade(tmp_path):
    memory, atom = _memory(tmp_path)
    meta_set(memory.store.conn, META_SCHEMA_DOWNGRADE_SEEN, "4")
    memory.store.conn.commit()

    receipt = memory.forget(atom, reason="user asked")
    memory.close()

    assert receipt["residue"]["schema_downgrade"]["stamped"] == 4
    assert "older mneme" in receipt["residue"]["schema_downgrade"]["note"]


def test_a_store_never_downgraded_says_so(tmp_path):
    memory, atom = _memory(tmp_path)

    receipt = memory.forget(atom, reason="user asked")
    memory.close()

    assert receipt["residue"]["schema_downgrade"]["stamped"] is None
