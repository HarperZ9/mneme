"""Erase by subject: every record of one user, with a receipt a third party can check.

The store holds two users across several sessions and every layer. After
`forget_subject` no file may hold the subject's marker or the subject's name
(forensic_util.py), the other user's rows must be untouched, and the receipt
must verify against the audit chain, fail when edited, and hold neither text
nor the user's name. The planted control leaves one of the subject's rows
behind and must be reported by the receipt and by the raw search.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import tempfile

import pytest

import forensic_util
from mneme import AgentMemory, cli, erase_subject
from mneme.erase import CollateralError, ErasedTextInReasonError, EraseTargetNotFound


def _store(tmp_path, marker: str, subject: str):
    db = tmp_path / "store" / "mem.db"
    db.parent.mkdir()
    memory = AgentMemory(db)
    memory.remember("home", [
        {"role": "user", "text": f"I live at {marker} street in Denver."},
        {"role": "assistant", "text": f"Noted the address {marker}."},
        {"role": "user", "text": "ok thanks"},
    ], user=subject)
    memory.remember("work", [{"role": "user", "text": f"I work at {marker} labs."}],
                    user=subject)
    memory.remember("home", [{"role": "user", "text": "I prefer oolong tea at dawn."},
                             {"role": "assistant", "text": "Oolong, got it."}], user="bob")
    memory.build_scenarios("home", user=subject)
    memory.persona("home", user=subject)
    memory.persona("home", user="bob")
    return db, memory


def _counts(memory: AgentMemory, user: str) -> tuple[int, int]:
    conn = memory.store.conn
    return (conn.execute('SELECT COUNT(*) FROM memories WHERE "user"=?', (user,)).fetchone()[0],
            conn.execute('SELECT COUNT(*) FROM turns WHERE "user"=?', (user,)).fetchone()[0])


def _roots(tmp_path, snapshot_root):
    return [tmp_path, snapshot_root.parent.parent, tempfile.gettempdir()]


def test_forget_subject_removes_every_record_of_that_user_and_no_other(
        tmp_path, snapshot_root):
    marker = forensic_util.new_marker()
    subject = "subject-" + forensic_util.new_marker("name")
    db, memory = _store(tmp_path, marker, subject)
    assert _counts(memory, subject) == (4, 4)          # 2 atoms, scenario, persona; 4 turns
    bob_before = _counts(memory, "bob")
    bob_rows = [tuple(r) for r in memory.store.conn.execute(
        'SELECT * FROM memories WHERE "user"=\'bob\' ORDER BY id')]

    receipt = memory.forget_subject(subject, reason="erasure request 41")

    assert _counts(memory, subject) == (0, 0)
    assert _counts(memory, "bob") == bob_before == (2, 2)
    assert [tuple(r) for r in memory.store.conn.execute(
        'SELECT * FROM memories WHERE "user"=\'bob\' ORDER BY id')] == bob_rows
    assert receipt["status"] == "erased" and receipt["findings"] == []
    assert receipt["counts"]["turns"] == 4 and receipt["counts"]["users"] == 1
    memory.close()
    roots = _roots(tmp_path, snapshot_root)
    assert forensic_util.hits(marker, *roots) == []
    assert forensic_util.hits(subject, *roots) == [], "the subject's name is still on disk"


def test_the_receipt_verifies_and_an_edited_receipt_does_not(tmp_path):
    marker, subject = forensic_util.new_marker(), "carol@example.test"
    db, memory = _store(tmp_path, marker, subject)
    receipt = memory.forget_subject(subject, reason="erasure request 42", emit_opening=True)
    salt = receipt["opening"]["subject_salt"]

    text = json.dumps(receipt)
    assert marker not in text and subject not in text and "carol" not in text
    assert str(db.parent) not in text
    assert "does not show that the operator met a legal duty" in receipt["boundary"]

    plain = memory.verify_subject_erasure(receipt)
    assert plain["ok"] is True
    assert plain["checks"] == {"receipt_hash": True, "audit_chain": True,
                               "subject_entry": True, "erase_entries": True}
    opened = memory.verify_subject_erasure(receipt, user=subject, salt=salt)
    assert opened["ok"] and opened["checks"]["subject_commitment"] is True
    assert opened["checks"]["subject_rows_absent"] is True
    wrong = memory.verify_subject_erasure(receipt, user="bob", salt=salt)
    assert wrong["ok"] is False and wrong["checks"]["subject_commitment"] is False
    assert wrong["checks"]["subject_rows_absent"] is False        # bob's rows are there

    edited = copy.deepcopy(receipt)
    edited["counts"]["turns"] = 0
    assert memory.verify_subject_erasure(edited)["checks"]["receipt_hash"] is False
    forged = copy.deepcopy(receipt)
    forged["erase_refs"] = forged["erase_refs"][:-1]
    forged["receipt_sha256"] = erase_subject.receipt_digest(forged)
    assert memory.verify_subject_erasure(forged)["checks"]["subject_entry"] is False
    memory.close()


def test_the_audit_log_proves_the_subject_erasure_and_cannot_drop_it(tmp_path):
    marker, subject = forensic_util.new_marker(), "dave-the-subject"
    db, memory = _store(tmp_path, marker, subject)
    receipt = memory.forget_subject(subject, reason="erasure request 43")
    rows = memory.store.audit_log()
    entry = next(r for r in rows if r["op"] == "erase_subject")
    assert entry["memory_id"] == receipt["subject"]["ref"]
    assert entry["before_sha"] == receipt["subject"]["commitment"]
    assert entry["entry_sha"] == receipt["audit"]["head"] == receipt["audit"]["entry_sha"]
    blob = json.dumps([tuple(r) for r in rows])
    assert subject not in blob and marker not in blob
    memory.close()
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM audit WHERE op='erase_subject'")
    conn.commit()
    conn.close()
    memory = AgentMemory(db)
    result = memory.verify_subject_erasure(receipt)
    assert result["ok"] is False
    assert result["checks"]["audit_chain"] is False
    assert result["checks"]["subject_entry"] is False
    memory.close()


def test_refusals_change_nothing(tmp_path):
    marker, subject = forensic_util.new_marker(), "erin-the-subject"
    db, memory = _store(tmp_path, marker, subject)
    before = _counts(memory, subject)
    with pytest.raises(ErasedTextInReasonError):
        memory.forget_subject(subject, reason=f"request from {subject}")
    with pytest.raises(EraseTargetNotFound):
        memory.forget_subject("nobody-here")
    # a memory of bob's that cites one of the subject's turns is another user's row
    turn = memory.store.conn.execute(
        'SELECT id FROM turns WHERE "user"=? AND role=\'user\'', (subject,)).fetchone()[0]
    memory.store.add_memory("bob-cites", "L1", "bob quotes the subject", [turn],
                            "fixture/v1", "fixture", session="home", user="bob")
    with pytest.raises(CollateralError):
        memory.forget_subject(subject, reason="erasure request 44")
    assert _counts(memory, subject) == before
    assert [r["op"] for r in memory.store.audit_log()] == []
    receipt = memory.forget_subject(subject, reason="erasure request 44",
                                    allow_collateral=True)
    assert _counts(memory, subject) == (0, 0)
    assert memory.store.memory("bob-cites") is None
    assert receipt["counts"]["users"] == 2
    memory.close()


def test_a_turn_from_before_schema_6_that_nobody_can_attribute_is_reported(tmp_path):
    marker, subject = forensic_util.new_marker(), "frank-the-subject"
    db, memory = _store(tmp_path, marker, subject)
    conn = memory.store.conn
    conn.execute('UPDATE turns SET "user"=NULL')       # as an older mneme wrote them
    conn.commit()

    receipt = memory.forget_subject(subject, reason="erasure request 45")

    # "home" is shared with bob, so its uncited turns (assistant lines, "ok
    # thanks") have no owner anyone can show; the cited ones still go
    assert receipt["counts"]["unattributed_turns"] == 3
    assert "turns_not_attributable" in receipt["findings"]
    assert receipt["status"] == "erased_residue_found"
    assert conn.execute('SELECT COUNT(*) FROM memories WHERE "user"=?',
                        (subject,)).fetchone()[0] == 0
    left = [r["text"] for r in memory.store.turns()]
    assert not any("street in Denver" in t or "labs" in t for t in left)
    memory.close()


def test_control_a_subject_erase_that_misses_a_row_is_caught(tmp_path, snapshot_root,
                                                              monkeypatch):
    """Planted: the selection drops one of the subject's memories."""
    marker, subject = forensic_util.new_marker(), "grace-the-subject"
    db, memory = _store(tmp_path, marker, subject)
    real = erase_subject.select

    def lossy(store, user):
        selection, unattributed = real(store, user)
        work = next(r["id"] for r in store.memories(session="work", user=user))
        kept = tuple(m for m in selection.memories if m != work)
        turns = tuple(t for t in selection.turns
                      if t not in json.loads(store.memory(work)["source_ids"]))
        return type(selection)(memories=kept, turns=turns), unattributed

    monkeypatch.setattr(erase_subject, "select", lossy)
    receipt = memory.forget_subject(subject, reason="erasure request 46")
    assert "subject_rows_present" in receipt["findings"]
    assert receipt["status"] == "erased_residue_found"
    check = memory.verify_subject_erasure(receipt, user=subject,
                                          salt="00" * 32)
    assert check["checks"]["subject_rows_absent"] is False
    memory.close()
    assert any(h.startswith("mem.db") for h in
               forensic_util.hits(marker, *_roots(tmp_path, snapshot_root)))


def test_the_cli_erases_a_subject_and_verifies_the_receipt(tmp_path, capsys):
    marker, subject = forensic_util.new_marker(), "heidi-the-subject"
    db, memory = _store(tmp_path, marker, subject)
    memory.close()
    state = ["--state", str(db)]

    assert cli.main([*state, "forget-subject", subject, "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["counts"]["turns"] == 4 and plan["other_users"] == 0
    assert marker not in json.dumps(plan) and subject not in json.dumps(plan)
    assert cli.main([*state, "forget-subject", subject]) == 1       # not a terminal: asks
    capsys.readouterr()
    assert cli.main([*state, "forget-subject", subject, "--yes", "--emit-opening"]) == 1
    assert "only to a terminal" in capsys.readouterr().err
    assert cli.main([*state, "forget-subject", "nobody-here", "--yes"]) == 2
    capsys.readouterr()

    assert cli.main([*state, "forget-subject", subject, "--yes",
                     "--reason", "erasure request 47"]) == 0
    out = capsys.readouterr().out
    receipt = json.loads(out[out.index('{\n  "schema": "mneme.subject-erasure-receipt/1"'):])
    assert receipt["status"] == "erased"
    saved = tmp_path / "receipt.json"
    saved.write_text(json.dumps(receipt), encoding="utf-8")
    assert cli.main([*state, "verify-erasure", str(saved)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    receipt["counts"]["turns"] = 99
    saved.write_text(json.dumps(receipt), encoding="utf-8")
    assert cli.main([*state, "verify-erasure", str(saved)]) == 1
    capsys.readouterr()
    assert cli.main([*state, "verify-erasure", str(tmp_path / "absent.json")]) == 2
