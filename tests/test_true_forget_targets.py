"""Falsifiers for erase targets, plan digests and the library contract.

A turn or a session named directly has no collateral: everything derived from
it is part of what the caller named. A plan is bound to its digest, so a store
that changed between plan and apply is refused rather than erased by guess.
"""
from __future__ import annotations

import pytest

from mneme import AgentMemory
from mneme.erase import (
    EraseTargetNotFound,
    Selection,
    StalePlanError,
    apply_erase,
    plan_erase,
)

TWO_FACTS = [
    {"id": "t1", "role": "user", "text": "My name is Dana. I live in Denver."},
    {"id": "t2", "role": "user", "text": "I prefer dark roast coffee."},
]


def _memory(path=":memory:"):
    memory = AgentMemory(path)
    memory.remember("s", TWO_FACTS)
    memory.build_scenarios("s")
    return memory


def _atom(memory, needle: str) -> str:
    return next(r["id"] for r in memory.store.memories(layer="L1")
                if needle in r["text"])


def _apply(memory, selection, reason="user asked"):
    plan = plan_erase(memory.store, selection)
    return plan, apply_erase(memory.store, selection, plan["plan_sha256"], reason=reason)


def test_a_turn_target_erases_every_memory_derived_from_it_with_no_collateral():
    memory = _memory()

    plan, receipt = _apply(memory, Selection(turns=("t1",)))

    assert plan["collateral"] == []
    assert receipt["counts"]["turns"] == 1
    assert {r["text"] for r in memory.store.memories()} == {"I prefer dark roast coffee."}
    assert [t["id"] for t in memory.store.turns()] == ["t2"]


def test_a_session_target_erases_that_session_and_keeps_the_others():
    memory = _memory()
    memory.remember("other", [{"id": "o1", "role": "user", "text": "I use Linux daily."}])

    _plan, receipt = _apply(memory, Selection(sessions=("s",)))

    assert receipt["counts"]["turns"] == 2
    assert [t["id"] for t in memory.store.turns()] == ["o1"]
    assert {r["text"] for r in memory.store.memories()} == {"I use Linux daily."}


def test_keep_sources_leaves_the_turn_and_the_receipt_says_so():
    memory = _memory()
    denver = _atom(memory, "Denver")

    receipt = memory.forget(denver, reason="user asked", include_sources=False)

    assert memory.store.turn("t1") is not None
    assert memory.store.memory(_atom(memory, "Dana")) is not None     # no collateral
    assert receipt["keep_sources"] is True
    assert receipt["counts"]["turns"] == 0 and receipt["counts"]["collateral"] == 0


def test_a_plan_digest_is_stable_and_a_changed_store_makes_it_stale():
    memory = _memory()
    selection = Selection(memories=(_atom(memory, "Denver"),))
    first = plan_erase(memory.store, selection)
    assert plan_erase(memory.store, selection)["plan_sha256"] == first["plan_sha256"]

    memory.store.add_memory("late", "L1", "Dana again", ["t1"], "custom/v1",
                            "atomic user fact", session="s")
    with pytest.raises(StalePlanError):
        apply_erase(memory.store, selection, first["plan_sha256"], reason="user asked")

    assert memory.store.turn("t1") is not None
    assert memory.audit()["entries"] == 0


def test_a_missing_target_is_named_and_nothing_is_written():
    memory = _memory()

    with pytest.raises(EraseTargetNotFound, match="turn"):
        plan_erase(memory.store, Selection(turns=("nope",)))
    assert memory.forget("does-not-exist") is None
    assert memory.audit()["entries"] == 0


def test_an_empty_selection_is_refused():
    memory = _memory()

    with pytest.raises(ValueError, match="nothing selected"):
        plan_erase(memory.store, Selection())


def test_erase_refuses_a_read_only_store(tmp_path):
    db = tmp_path / "mneme.db"
    _memory(db).close()
    reader = AgentMemory(db, read_only=True)
    selection = Selection(turns=("t1",))
    plan = plan_erase(reader.store, selection)

    with pytest.raises(ValueError, match="writable"):
        apply_erase(reader.store, selection, plan["plan_sha256"], reason="user asked")
    reader.close()


def test_the_plan_names_every_user_whose_memories_it_erases():
    memory = AgentMemory(":memory:")
    memory.remember("chat1", [{"role": "user", "text": "I live in Denver."}], user="alice")
    memory.remember("chat1", [{"role": "user", "text": "I live in Boston."}], user="bob")

    plan = plan_erase(memory.store, Selection(sessions=("chat1",)))

    assert plan["users"] == ["alice", "bob"]
    assert plan["counts"]["turns"] == 2


def test_the_plan_carries_no_text_unless_previews_are_asked_for():
    memory = _memory()
    selection = Selection(memories=(_atom(memory, "Denver"),))

    plain = plan_erase(memory.store, selection)
    shown = plan_erase(memory.store, selection, previews=True)

    assert "Dana" not in repr(plain) and "Denver" not in repr(plain)
    assert shown["previews"][_atom(memory, "Dana")] == "My name is Dana."
    assert shown["plan_sha256"] == plain["plan_sha256"]


def test_an_open_transaction_is_refused_rather_than_committed(tmp_path):
    memory = _memory(tmp_path / "mneme.db")
    selection = Selection(turns=("t1",))
    plan = plan_erase(memory.store, selection)
    memory.store.conn.execute("INSERT INTO meta(key,value) VALUES('probe','x')")

    with pytest.raises(RuntimeError, match="open transaction"):
        apply_erase(memory.store, selection, plan["plan_sha256"], reason="user asked")
    memory.store.conn.rollback()
    assert memory.store.turn("t1") is not None
