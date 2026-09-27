"""Falsifiers for true forget: erase removes the raw turn and every derived form.

The Dana and Denver fixture from the older forget tests is used twice. The
original one-sentence turn yields one atom. The two-sentence variant yields a
name atom and a Denver atom from the same turn, which is what makes the name
atom collateral when the Denver atom is forgotten with its source turn.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from mneme import AgentMemory
from mneme.erase import (
    CollateralError,
    ErasedTextInReasonError,
    Selection,
    plan_erase,
)
from mneme.receipt import content_hash

ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "verify_audit.py"
ORIGINAL = [
    {"id": "t1", "role": "user", "text": "My name is Dana and I live in Denver."},
    {"id": "t2", "role": "user", "text": "I prefer dark roast coffee."},
]
TWO_FACTS = [
    {"id": "t1", "role": "user", "text": "My name is Dana. I live in Denver."},
    {"id": "t2", "role": "user", "text": "I prefer dark roast coffee."},
]


def _memory(path=":memory:", turns=TWO_FACTS):
    memory = AgentMemory(path)
    memory.remember("s", turns)
    return memory


def _atom(memory, needle: str) -> str:
    return next(r["id"] for r in memory.store.memories(layer="L1")
                if needle in r["text"])


def _all_text(memory) -> list[str]:
    rows = memory.store.conn.execute(
        "SELECT text FROM turns UNION ALL SELECT text FROM memories").fetchall()
    return [row[0] for row in rows]


def test_plan_for_the_denver_atom_names_its_turn_and_the_collateral_name_atom():
    memory = _memory()
    denver, name = _atom(memory, "Denver"), _atom(memory, "Dana")

    plan = plan_erase(memory.store, Selection(memories=(denver,)))

    assert plan["turns"] == ["t1"]
    assert set(plan["memories"]) == {denver, name}
    assert plan["collateral"] == [name]
    assert plan["counts"]["collateral"] == 1
    assert re.fullmatch(r"[0-9a-f]{64}", plan["plan_sha256"])
    assert memory.store.turn("t1") is not None          # planning deletes nothing
    assert memory.store.memory(denver) is not None


def test_the_original_one_sentence_fixture_has_no_collateral():
    memory = _memory(turns=ORIGINAL)
    atom = _atom(memory, "Denver")

    plan = plan_erase(memory.store, Selection(memories=(atom,)))

    assert plan["turns"] == ["t1"] and plan["memories"] == [atom]
    assert plan["collateral"] == []


def test_library_forget_refuses_collateral_without_consent_and_carries_the_plan():
    memory = _memory()
    denver, name = _atom(memory, "Denver"), _atom(memory, "Dana")

    with pytest.raises(CollateralError) as caught:
        memory.forget(denver, reason="user asked")

    assert caught.value.plan["collateral"] == [name]
    assert memory.store.turn("t1") is not None and memory.store.memory(name) is not None
    assert memory.audit()["entries"] == 0
    receipt = memory.forget(denver, reason="user asked", allow_collateral=True)
    assert receipt["counts"]["turns"] == 1
    assert receipt["counts"]["collateral"] == 1


def test_after_apply_no_row_and_no_database_byte_holds_the_erased_text(tmp_path):
    db = tmp_path / "mneme.db"
    memory = _memory(db)

    memory.forget(_atom(memory, "Denver"), reason="user asked", allow_collateral=True)

    texts = _all_text(memory)
    assert "I prefer dark roast coffee." in texts        # unrelated rows survive
    assert all("Denver" not in t and "Dana" not in t for t in texts)
    memory.close()
    for path in [db, *(Path(f"{db}{s}") for s in ("-journal", "-wal", "-shm"))]:
        if path.exists():
            data = path.read_bytes()
            for word in ("Denver", "Dana"):
                assert word.encode("utf-8") not in data, path.name
                assert word.encode("utf-16-le") not in data, path.name


def test_audit_verifies_in_the_library_and_the_standalone_verifier(tmp_path):
    db = tmp_path / "mneme.db"
    memory = _memory(db)
    memory.forget(_atom(memory, "Denver"), reason="user asked", allow_collateral=True)

    log = memory.audit()
    assert log["chain_intact"] is True
    assert {e["op"] for e in log["log"]} == {"erase"}
    assert {"L0", "L1"} <= {e["layer"] for e in log["log"]}
    memory.close()
    proc = subprocess.run([sys.executable, str(VERIFIER), str(db)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.startswith("MATCH")


def test_erase_rows_use_random_refs_and_only_earlier_rows_keep_the_content_id(tmp_path):
    db = tmp_path / "mneme.db"
    memory = _memory(db)
    denver = _atom(memory, "Denver")
    memory.update(denver, "My name is Dana. I live in Denver, Colorado.", reason="precise")
    earlier = [row["ord"] for row in memory.store.audit_log()]

    receipt = memory.forget(denver, reason="user asked", allow_collateral=True)

    rows = memory.store.audit_log()
    erase_rows = [row for row in rows if row["op"] == "erase"]
    assert len(erase_rows) == 3                           # two atoms and one turn
    assert all(re.fullmatch(r"er_[0-9a-f]{24}", r["memory_id"]) for r in erase_rows)
    naming = [row["ord"] for row in rows if denver in "|".join(map(str, tuple(row)))]
    assert naming == earlier and len(earlier) == 1
    assert receipt["residue"]["legacy_audit_rows"]["count"] == 1
    memory.close()
    assert db.read_bytes().count(denver.encode("ascii")) == 1


def test_erase_before_value_is_neither_a_content_hash_nor_a_digest_of_the_text():
    memory = _memory(turns=ORIGINAL)
    atom = _atom(memory, "Denver")
    row, turn = memory.store.memory(atom), memory.store.turn("t1")

    memory.forget(atom, reason="user asked")

    erase_rows = [r for r in memory.store.audit_log() if r["op"] == "erase"]
    befores = {r["before_sha"] for r in erase_rows}
    forbidden = {row["content_sha256"], turn["content_sha256"],
                 content_hash(row["text"]), content_hash(turn["text"])}
    forbidden |= {hashlib.sha256(t.encode("utf-8")).hexdigest()
                  for t in (row["text"], turn["text"])}
    assert len(befores) == len(erase_rows) == 2
    assert all(re.fullmatch(r"[0-9a-f]{64}", b) for b in befores)
    assert not befores & forbidden
    assert all(r["after_sha"] == "" for r in erase_rows)


def test_scenario_and_persona_rows_that_cite_the_atom_are_gone():
    memory = _memory()
    memory.build_scenarios("s")
    memory.persona("s")
    denver = _atom(memory, "Denver")
    derived = {r["id"] for r in memory.store.memories(layer="L2") if "Denver" in r["text"]}
    derived |= {r["id"] for r in memory.store.memories(layer="L3")}

    plan = plan_erase(memory.store, Selection(memories=(denver,)))
    memory.forget(denver, reason="user asked", allow_collateral=True)

    assert derived <= set(plan["memories"])
    assert all(memory.store.memory(mid) is None for mid in derived)
    assert memory.store.memories(layer="L3") == []
    assert [r["text"] for r in memory.store.memories(layer="L2")] == [
        "I prefer dark roast coffee."]


def _lived():
    memory = AgentMemory(":memory:")
    memory.remember("s", [{"id": "t1", "role": "user", "text": "I live in Denver."}],
                    user="a")
    denver = memory.store.memories(layer="L1", user="a")[0]["id"]
    seattle = memory.supersede(denver, "I live in Seattle.", reason="moved")["new_id"]
    return memory, denver, seattle


@pytest.mark.parametrize("target", ["older", "newer"])
def test_supersession_lineage_is_erased_in_both_directions(target):
    memory, denver, seattle = _lived()
    chosen, other = (denver, seattle) if target == "older" else (seattle, denver)
    source_turns = [t["id"] for t in memory.store.turns()]    # one partitioned id

    plan = plan_erase(memory.store, Selection(memories=(chosen,)))
    memory.forget(chosen, reason="user asked")

    assert set(plan["memories"]) == {denver, seattle}
    assert plan["lineage"] == [other]
    assert plan["turns"] == source_turns and len(source_turns) == 1   # same raw source
    assert memory.store.memories(include_superseded=True) == []
    assert memory.store.turns() == []
    assert memory.history(predicate="lives_in", user="a")["timeline"] == []


@pytest.mark.parametrize("reason", [
    "My name is Dana and I live in Denver.",
    "user said: I live in Denver.",
    "i LIVE in   denver, please",
])
def test_a_reason_that_repeats_erased_text_is_refused(reason):
    memory = _memory(turns=ORIGINAL)
    atom = _atom(memory, "Denver")

    with pytest.raises(ErasedTextInReasonError) as caught:
        memory.forget(atom, reason=reason)

    assert "Denver" not in str(caught.value)             # the refusal does not echo it
    assert memory.store.memory(atom) is not None
    assert memory.audit()["entries"] == 0


def test_a_reason_that_repeats_a_short_erased_text_whole_is_refused():
    memory = AgentMemory(":memory:")
    memory.remember("s", [{"id": "t1", "role": "user", "text": "I am Bo."}])
    atom = memory.store.memories(layer="L1")[0]["id"]

    with pytest.raises(ErasedTextInReasonError):
        memory.forget(atom, reason="delete 'I am Bo.' now")
    assert memory.forget(atom, reason="user asked")["counts"]["turns"] == 1


def test_an_injected_failure_before_commit_changes_nothing(tmp_path, monkeypatch):
    import mneme.erase as erase_module

    db = tmp_path / "mneme.db"
    memory = _memory(db, turns=ORIGINAL)
    atom = _atom(memory, "Denver")

    def fail(conn):
        raise RuntimeError("injected failure before commit")

    monkeypatch.setattr(erase_module, "_commit", fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        memory.forget(atom, reason="user asked")
    memory.close()

    again = AgentMemory(db)
    assert again.store.turn("t1") is not None and again.store.memory(atom) is not None
    log = again.audit()
    assert log["entries"] == 0 and log["chain_intact"] is True


def test_a_process_crash_after_the_audit_rows_and_before_commit_changes_nothing(
        tmp_path, snapshot_root):
    db = tmp_path / "mneme.db"
    _memory(db, turns=ORIGINAL).close()
    child = textwrap.dedent("""
        import os, sys
        from pathlib import Path
        sys.path.insert(0, sys.argv[2])
        import mneme.erase as erase_module
        import mneme.snapshot_dir as snapshot_dir
        from mneme import AgentMemory
        snapshot_dir.platform_snapshot_root = lambda: Path(sys.argv[3])
        memory = AgentMemory(sys.argv[1])
        atom = next(r["id"] for r in memory.store.memories(layer="L1"))
        def crash(conn):
            rows = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
            print(rows, flush=True)
            os._exit(17)
        erase_module._commit = crash
        memory.forget(atom, reason="user asked")
    """)
    proc = subprocess.run(
        [sys.executable, "-c", child, str(db), str(ROOT / "src"), str(snapshot_root)],
        capture_output=True, text=True, timeout=60)

    assert proc.returncode == 17, proc.stderr
    assert proc.stdout.strip() == "2"                   # rows existed, uncommitted
    again = AgentMemory(db)                              # the hot journal rolls back
    assert again.store.turn("t1") is not None
    assert len(again.store.memories(layer="L1")) == 2
    assert again.audit()["entries"] == 0 and again.audit()["chain_intact"] is True
