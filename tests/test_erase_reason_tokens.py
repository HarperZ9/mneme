"""Falsifiers for an erase reason that keeps a short secret from the erased text.

The reason is stored verbatim in an append-only, hash-chained log. Before this
change a reason passed when it shared no 16-character run with the erased
text, so `rotated FAKE-KEY-7Q2Z9` stored the key for good while the receipt
said `erased`. A reason may now not repeat a credential-shaped token of the
erased text (8 or more characters, or 6 or more with a digit), and a short
reason may not sit whole inside it. Every value here is planted test data.
"""
from __future__ import annotations

import pytest

from mneme import AgentMemory
from mneme.erase import ErasedTextInReasonError, Selection, apply_erase, plan_erase

KEY = "FAKE-KEY-7Q2Z9"
KEY_TURN = f"My API key is {KEY} for the staging box."


def _store(text: str):
    memory = AgentMemory(":memory:")
    memory.remember("s", [{"id": "t1", "role": "user", "text": text},
                          {"id": "t2", "role": "user", "text": "I prefer green tea daily."}])
    selection = Selection(turns=("t1",))
    return memory, selection, plan_erase(memory.store, selection)


@pytest.mark.parametrize("reason", [f"rotated {KEY}", KEY, f"old key {KEY.lower()} gone",
                                    "for the staging"])
def test_a_reason_holding_a_token_or_a_whole_short_run_of_the_text_is_refused(reason):
    memory, selection, plan = _store(KEY_TURN)

    with pytest.raises(ErasedTextInReasonError) as caught:
        apply_erase(memory.store, selection, plan["plan_sha256"], reason=reason)

    assert KEY.lower() not in str(caught.value).lower()
    assert memory.store.turn("t1") is not None
    assert memory.store.audit_log() == []


@pytest.mark.parametrize("text, reason", [
    (KEY_TURN, "user asked"),
    (KEY_TURN, "ticket 4711"),
    ("I live in Denver near the park.", "moving away from Denver"),
    ("Bo", "moved to Boston"),
])
def test_an_ordinary_reason_is_allowed(text, reason):
    memory, selection, plan = _store(text)

    receipt = apply_erase(memory.store, selection, plan["plan_sha256"], reason=reason)

    assert receipt["status"] == "erased"
    assert memory.store.turn("t1") is None


def test_a_short_erased_text_is_refused_only_as_a_whole_word():
    memory, selection, plan = _store("Bo")

    with pytest.raises(ErasedTextInReasonError):
        apply_erase(memory.store, selection, plan["plan_sha256"], reason="Bo asked")


def test_an_earlier_reason_holding_only_the_key_is_residue():
    memory = AgentMemory(":memory:")
    memory.remember("s", [{"id": "t1", "role": "user", "text": KEY_TURN},
                          {"id": "t2", "role": "user", "text": "I prefer green tea daily."}])
    tea = next(r["id"] for r in memory.store.memories(layer="L1") if "tea" in r["text"])
    memory.update(tea, "I prefer black tea daily.", reason=f"see {KEY}")
    plan = plan_erase(memory.store, Selection(turns=("t1",)))

    receipt = apply_erase(memory.store, Selection(turns=("t1",)), plan["plan_sha256"],
                          reason="user asked")

    assert receipt["residue"]["audit_reasons_with_erased_text"]["count"] == 1
    assert "audit_reasons_quote_erased_text" in receipt["findings"]
    assert receipt["status"] == "erased_residue_found"
