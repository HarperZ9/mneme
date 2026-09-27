"""Falsifiers for audit reasons that could confirm a guess of erased text.

An erase reason is stored verbatim. A reason that carries the content-derived
id or content hash of an erased row would let the new erase entry confirm a
guessed text, so those are refused like the text itself. The default
supersede reason used to name the new memory's content-derived id; it now
names nothing. Every value here is planted test data.
"""
from __future__ import annotations

import pytest

from mneme import AgentMemory
from mneme.erase import ErasedTextInReasonError, Selection, apply_erase, plan_erase

ONE_FACT = [{"id": "t1", "role": "user", "text": "I live in Denver near the park."}]


def _setup():
    memory = AgentMemory(":memory:")
    memory.remember("s", ONE_FACT)
    atom = memory.store.memories(layer="L1")[0]
    selection = Selection(memories=(atom["id"],))
    return memory, atom, selection, plan_erase(memory.store, selection)


def _reasons(atom, plan, turn_sha: str) -> list[str]:
    return [f"forget {atom['id']}",
            f"memory {atom['id'][:12]} please",
            f"sha {atom['content_sha256']}",
            f"turn digest {turn_sha[:12]}",
            f"plan {plan['plan_sha256'][:20]}",
            f"FORGET {atom['id'].upper()}"]


def test_a_reason_naming_an_erased_id_or_hash_is_refused_and_nothing_changes():
    memory, atom, selection, plan = _setup()
    turn_sha = memory.store.turn("t1")["content_sha256"]

    for reason in _reasons(atom, plan, turn_sha):
        with pytest.raises(ErasedTextInReasonError):
            apply_erase(memory.store, selection, plan["plan_sha256"], reason=reason)

    assert memory.store.memory(atom["id"]) is not None
    assert memory.store.audit_log() == []


def test_a_short_hex_run_and_an_unrelated_ticket_number_are_allowed():
    memory, atom, selection, plan = _setup()

    receipt = apply_erase(memory.store, selection, plan["plan_sha256"],
                          reason=f"ticket 4711, ref {atom['id'][:11]}")

    assert receipt["status"] == "erased"


def test_the_default_supersede_reason_names_no_id():
    memory = AgentMemory(":memory:")
    memory.remember("s", ONE_FACT)
    old = memory.store.memories(layer="L1")[0]["id"]
    new = "0123456789abcdef"

    entry = memory.store.supersede(old, new, reason="")

    assert new not in entry["reason"] and old not in entry["reason"]
    assert entry["reason"] == "superseded"
