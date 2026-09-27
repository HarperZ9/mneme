"""Falsifiers for a receipt that said `erased` while kept rows held the text.

Two cases passed before this change. A short erased text (a planted 12-byte
key) inside a longer kept row matched nothing, because a text under 16 bytes
had to equal a row whole. A 16-byte run of a long erased turn inside a kept
row was only counted as overlap, which is not a finding. Both now keep the
receipt from saying `erased`. Strings in a table mneme does not keep are no
longer treated as kept, so the scan reports them. Every value is planted.
"""
from __future__ import annotations

from mneme import AgentMemory
from mneme.erase import Selection, apply_erase, plan_erase

TEA = "I prefer green tea daily."


def _erase_turn(memory, turn_id: str) -> dict:
    selection = Selection(turns=(turn_id,))
    plan = plan_erase(memory.store, selection)
    return apply_erase(memory.store, selection, plan["plan_sha256"], reason="user asked")


def test_a_short_erased_text_inside_a_kept_row_is_residue(tmp_path):
    memory = AgentMemory(tmp_path / "mneme.db")
    memory.remember("s", [{"id": "t1", "role": "user", "text": "FAKE-SHORT-9"},
                          {"id": "t2", "role": "user",
                           "text": "note to self FAKE-SHORT-9 is the code"}])

    receipt = _erase_turn(memory, "t1")

    assert "kept_rows_contain_short_text" in receipt["findings"]
    assert receipt["residue"]["kept_rows_with_short_text"]["rows"] >= 1
    assert receipt["status"] == "erased_residue_found"
    memory.close()


def test_a_kept_row_sharing_a_run_of_an_erased_turn_is_residue(tmp_path):
    key = "FAKE-LONG-KEY-0123456789-ABCDEF"
    memory = AgentMemory(tmp_path / "mneme.db")
    memory.remember("s", [
        {"id": "t1", "role": "assistant", "text": f"Your new token is {key}, keep it safe."},
        {"id": "t2", "role": "assistant", "text": f"Stored {key} in the vault."},
        {"id": "t3", "role": "user", "text": TEA}])

    receipt = _erase_turn(memory, "t1")

    assert "kept_rows_share_erased_run" in receipt["findings"]
    assert receipt["residue"]["kept_rows_sharing_erased_run"]["rows"] == 1
    assert receipt["status"] == "erased_residue_found"
    memory.close()


def test_an_ordinary_short_word_in_a_kept_row_is_not_residue(tmp_path):
    memory = AgentMemory(tmp_path / "mneme.db")
    memory.remember("s", [{"id": "t1", "role": "user", "text": "Thanks."},
                          {"id": "t2", "role": "user", "text": "Thanks. " + TEA}])

    receipt = _erase_turn(memory, "t1")

    assert receipt["status"] == "erased"
    memory.close()


def test_text_in_a_table_mneme_does_not_keep_is_found_by_the_scan(tmp_path):
    canary = "I remember the planted canary sentence for the side table."
    memory = AgentMemory(tmp_path / "mneme.db")
    memory.remember("s", [{"id": "t1", "role": "user", "text": canary},
                          {"id": "t2", "role": "user", "text": TEA}])
    memory.store.conn.execute("CREATE TABLE side_notes (body TEXT)")
    memory.store.conn.execute("INSERT INTO side_notes VALUES (?)", (canary,))
    memory.store.conn.commit()

    receipt = _erase_turn(memory, "t1")

    assert receipt["scan"]["status"] == "hits"
    assert receipt["status"] == "erased_residue_found"
    memory.close()
