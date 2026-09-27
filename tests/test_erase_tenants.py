"""Falsifiers for which rows an erase plan offers as duplicates.

Turns carry no user column. A turn that no memory cites used to count as the
same user's for any user, so another tenant's uncited assistant turn was
offered as a "same user" duplicate. It now counts only when the memories of
its session belong to the plan's users, or when its session is one the erase
already touches. The other tests pin behaviour a wrong implementation would
break: a short text is a duplicate only of an equal row, and duplicates are
followed for more than one round. Every text is planted test data.
"""
from __future__ import annotations

from mneme import AgentMemory
from mneme.erase import Selection, apply_erase, plan_erase

SAME = "I live in Denver near the park."


def _turn(memory, session: str, role: str) -> str:
    return next(t["id"] for t in memory.store.turns()
                if t["session"] == session and t["role"] == role)


def _alice_atom(memory) -> str:
    return next(r["id"] for r in memory.store.memories(layer="L1", user="alice"))


def test_another_tenants_uncited_turn_is_not_a_duplicate(tmp_path):
    memory = AgentMemory(tmp_path / "mneme.db")
    memory.remember("a1", [{"id": "ta", "role": "user", "text": SAME}], user="alice")
    memory.remember("b1", [{"id": "tb0", "role": "user", "text": "I prefer green tea daily."},
                           {"id": "tb", "role": "assistant", "text": SAME}], user="bob")

    bob_turn = _turn(memory, "b1", "assistant")
    plan = plan_erase(memory.store, Selection(memories=(_alice_atom(memory),)))
    receipt = memory.forget(_alice_atom(memory), reason="user asked",
                            allow_collateral=True)

    assert plan["duplicates"]["turns"] == []
    assert plan["users"] == ["alice"]
    assert memory.store.turn(bob_turn) is not None
    assert receipt["residue"]["kept_rows_with_erased_text"]["rows"] == 1
    assert receipt["status"] == "erased_residue_found"
    memory.close()


def test_an_uncited_turn_in_the_same_users_session_is_a_duplicate(tmp_path):
    memory = AgentMemory(tmp_path / "mneme.db")
    memory.remember("a1", [{"id": "ta", "role": "user", "text": SAME},
                           {"id": "tz", "role": "assistant", "text": SAME}], user="alice")

    plan = plan_erase(memory.store, Selection(memories=(_alice_atom(memory),)))

    assert plan["duplicates"]["turns"] == [_turn(memory, "a1", "assistant")]
    memory.close()


def test_an_uncited_turn_of_a_session_without_memories_stays_unless_touched(tmp_path):
    memory = AgentMemory(tmp_path / "mneme.db")
    memory.remember("a1", [{"id": "ta", "role": "user", "text": SAME}], user="alice")
    memory.remember("x1", [{"id": "tx", "role": "assistant", "text": SAME}])

    plan = plan_erase(memory.store, Selection(memories=(_alice_atom(memory),)))

    assert plan["duplicates"]["turns"] == []
    memory.close()


def test_a_short_erased_text_leaves_a_longer_row_that_contains_it(tmp_path):
    memory = AgentMemory(tmp_path / "mneme.db")
    memory.remember("s", [{"id": "t1", "role": "user", "text": "Yes."},
                          {"id": "t2", "role": "user", "text": "Yes. I will come tomorrow."}])

    plan = plan_erase(memory.store, Selection(turns=("t1",)))
    receipt = apply_erase(memory.store, Selection(turns=("t1",)), plan["plan_sha256"])

    assert plan["duplicates"] == {"turns": [], "memories": []}
    assert memory.store.turn("t2") is not None
    assert receipt["status"] == "erased"
    memory.close()


def test_duplicates_are_followed_past_the_first_round(tmp_path):
    second = "I work at the harbor office downtown."
    memory = AgentMemory(tmp_path / "mneme.db")
    memory.remember("s", [{"id": "t1", "role": "user", "text": SAME},
                          {"id": "t2", "role": "user", "text": f"{SAME} {second}"},
                          {"id": "t3", "role": "assistant", "text": second},
                          {"id": "t4", "role": "user", "text": "I prefer green tea daily."}])
    atom = next(r["id"] for r in memory.store.memories(layer="L1") if "Denver" in r["text"])

    plan = plan_erase(memory.store, Selection(memories=(atom,)))

    assert plan["duplicates"]["turns"] == ["t2", "t3"]
    memory.close()


def test_a_shared_session_name_does_not_make_another_tenants_turn_a_duplicate(tmp_path):
    memory = AgentMemory(tmp_path / "mneme.db")
    memory.remember("chat", [{"id": "ta", "role": "user", "text": SAME}], user="alice")
    memory.remember("chat", [{"id": "tb0", "role": "user", "text": "I prefer green tea daily."},
                             {"id": "tb", "role": "assistant", "text": SAME}], user="bob")

    plan = plan_erase(memory.store, Selection(memories=(_alice_atom(memory),)))

    assert plan["duplicates"]["turns"] == []
    memory.close()
