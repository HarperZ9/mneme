"""Falsifiers for an erase receipt that says `erased` only when it is true.

Before this change the receipt said `erased` while the erased sentence was
still readable: in a second turn that said the same thing, in the source turn
of a duplicate that consolidation had merged away, and in an earlier audit
reason that quoted it. Each case below plants one of those copies and checks
that the plan takes it, or that the receipt names it and does not say
`erased`. Every text is planted test data.
"""
from __future__ import annotations

import pytest

from mneme import AgentMemory
from mneme.erase import CollateralError, Selection, plan_erase

SAME = "I live in Denver near the park."
OTHER = "I prefer green tea daily."


def _atoms(memory, needle: str) -> list[str]:
    return [r["id"] for r in memory.store.memories(layer="L1") if needle in r["text"]]


def _atom_citing(memory, turn_id: str) -> str:
    return next(r["id"] for r in memory.store.memories(layer="L1")
                if turn_id in r["source_ids"])


def test_a_sentence_said_twice_is_planned_as_a_duplicate_and_erased(tmp_path):
    db = tmp_path / "mneme.db"
    memory = AgentMemory(db)
    memory.remember("s", [{"id": "t1", "role": "user", "text": SAME},
                          {"id": "t2", "role": "user", "text": SAME},
                          {"id": "t3", "role": "user", "text": OTHER}])
    first, second = _atom_citing(memory, "t1"), _atom_citing(memory, "t2")

    plan = plan_erase(memory.store, Selection(memories=(first,)))
    assert plan["duplicates"] == {"turns": ["t2"], "memories": [second]}
    assert plan["counts"]["duplicates"] == 2
    with pytest.raises(CollateralError):
        memory.forget(first, reason="user asked")
    receipt = memory.forget(first, reason="user asked", allow_collateral=True)

    assert receipt["status"] == "erased"
    assert not memory.recall("Denver park", strategy="keyword").hits
    assert [t["id"] for t in memory.store.turns()] == ["t3"]
    memory.close()
    assert b"Denver near the park" not in db.read_bytes()


def test_a_copy_in_another_users_rows_is_named_and_the_status_is_not_erased(tmp_path):
    memory = AgentMemory(tmp_path / "mneme.db")
    memory.remember("a", [{"id": "t1", "role": "user", "text": SAME}], user="alice")
    memory.remember("b", [{"id": "t1", "role": "user", "text": SAME}], user="bob")
    alice = next(r["id"] for r in memory.store.memories(layer="L1", user="alice"))

    plan = plan_erase(memory.store, Selection(memories=(alice,)))
    receipt = memory.forget(alice, reason="user asked")

    assert plan["counts"]["duplicates"] == 0             # never another tenant's rows
    assert receipt["status"] == "erased_residue_found"
    assert receipt["residue"]["kept_rows_with_erased_text"]["rows"] == 2
    memory.close()


def _consolidated(tmp_path):
    memory = AgentMemory(tmp_path / "mneme.db")
    memory.remember("s", [{"id": "t1", "role": "user", "text": SAME},
                          {"id": "t2", "role": "user", "text": SAME[:-1] + " now."}])
    return memory


def test_consolidation_keeps_the_dropped_duplicates_source_turn_in_the_erase(tmp_path):
    memory = _consolidated(tmp_path)
    assert memory.consolidate("s")["merged_away"] == 1
    kept = memory.store.memories(layer="L1")[0]["id"]

    plan = plan_erase(memory.store, Selection(memories=(kept,)))
    receipt = memory.forget(kept, reason="user asked")

    assert set(plan["turns"]) == {"t1", "t2"}
    assert receipt["status"] == "erased"
    assert memory.store.turns() == []
    assert memory.store.conn.execute("SELECT COUNT(*) FROM merges").fetchone()[0] == 0
    memory.close()


def test_a_merge_written_without_a_link_is_reported_as_unresolved(tmp_path):
    memory = _consolidated(tmp_path)
    dropped, kept = _atom_citing(memory, "t1"), _atom_citing(memory, "t2")
    # the tombstone mneme 0.4.2 consolidation wrote: a reason, and no merge link
    memory.store.forget(dropped, reason=f"merged into {kept} (near-duplicate)")

    receipt = memory.forget(kept, reason="user asked")

    assert receipt["status"] == "erased_residue_found"
    assert receipt["residue"]["unresolved_merged_sources"]["count"] == 1
    assert memory.store.turn("t1") is not None
    memory.close()


def test_an_earlier_audit_reason_that_quotes_the_erased_text_is_residue(tmp_path):
    db = tmp_path / "mneme.db"
    memory = AgentMemory(db)
    memory.remember("s", [{"id": "t1", "role": "user", "text": SAME},
                          {"id": "t2", "role": "user", "text": OTHER}])
    atom = _atom_citing(memory, "t1")
    memory.update(atom, "I live in Colorado.", reason=f"user said: {SAME}")

    receipt = memory.forget(atom, reason="user asked")

    assert receipt["status"] == "erased_residue_found"
    assert receipt["residue"]["audit_reasons_with_erased_text"]["count"] == 1
    assert receipt["scan"]["status"] == "hits"
    memory.close()


def test_an_unreadable_store_file_makes_the_receipt_incomplete(tmp_path, monkeypatch):
    import mneme.erase_scan as erase_scan

    memory = AgentMemory(tmp_path / "mneme.db")
    memory.remember("s", [{"id": "t1", "role": "user", "text": SAME}])
    real = erase_scan.open_for_scan

    def deny(path, **kwargs):
        if path.name == "mneme.db":
            raise PermissionError("planted: read denied")
        return real(path, **kwargs)

    monkeypatch.setattr(erase_scan, "open_for_scan", deny)
    receipt = memory.forget(_atom_citing(memory, "t1"), reason="user asked")

    assert receipt["scan"]["status"] == "incomplete"
    assert receipt["scan"]["unreadable"] == ["mneme.db"]
    assert receipt["status"] == "incomplete"
    memory.close()


def test_a_scan_with_only_short_texts_says_structural_only(tmp_path):
    memory = AgentMemory(tmp_path / "mneme.db")
    memory.remember("s", [{"id": "t1", "role": "user", "text": "I am Bo."}])

    plan = plan_erase(memory.store, Selection(turns=("t1",)))
    from mneme.erase import apply_erase
    receipt = apply_erase(memory.store, Selection(turns=("t1",)), plan["plan_sha256"])

    assert receipt["scan"]["texts_scanned"] == 0
    assert receipt["scan"]["status"] == "structural_only"
    assert receipt["status"] == "erased"
    memory.close()


def test_kept_sources_are_named_and_the_status_says_so(tmp_path):
    memory = AgentMemory(tmp_path / "mneme.db")
    memory.remember("s", [{"id": "t1", "role": "user", "text": SAME}])

    receipt = memory.forget(_atom_citing(memory, "t1"), reason="user asked",
                            include_sources=False)

    assert receipt["status"] == "erased_sources_kept"
    assert receipt["residue"]["kept_sources"]["rows"] == 1
    assert memory.store.turn("t1") is not None
    memory.close()
