"""Falsifiers for the forget surfaces: CLI and MCP.

The CLI runs on the owner's machine, so it may show collateral text and, on
request, the openings of the erase commitments. An MCP result enters a model
context and reaches its provider, so the MCP tool returns ids and counts, shows
text only when asked, never returns an opening, and deletes nothing until the
caller confirms the exact plan digest.
"""
from __future__ import annotations

import hashlib
import json

import pytest

import mneme.cli_forget as cli_forget
from mneme import AgentMemory
from mneme.cli import main
from mneme.mcp import handle_request

TWO_FACTS = [
    {"id": "t1", "role": "user", "text": "My name is Dana. I live in Denver."},
    {"id": "t2", "role": "user", "text": "I prefer dark roast coffee."},
]
TEXTS = {"t1": TWO_FACTS[0]["text"], "Denver": "I live in Denver.",
         "Dana": "My name is Dana."}


def _db(tmp_path):
    db = tmp_path / "mneme.db"
    memory = AgentMemory(db)
    memory.remember("s", TWO_FACTS)
    ids = {needle: next(r["id"] for r in memory.store.memories(layer="L1")
                        if needle in r["text"]) for needle in ("Denver", "Dana")}
    memory.close()
    return db, ids


def _turn_exists(db) -> bool:
    memory = AgentMemory(db)
    try:
        return memory.store.turn("t1") is not None
    finally:
        memory.close()


def _forget(db, *args):
    return main(["--state", str(db), "forget", *args])


def test_cli_dry_run_prints_the_plan_with_local_previews_and_deletes_nothing(
        tmp_path, capsys):
    db, ids = _db(tmp_path)

    assert _forget(db, ids["Denver"], "--dry-run") == 0

    plan = json.loads(capsys.readouterr().out)
    assert plan["schema"] == "mneme.erase-plan/1"
    assert plan["collateral"] == [ids["Dana"]]
    assert plan["previews"][ids["Dana"]] == TEXTS["Dana"]
    assert _turn_exists(db)


def test_cli_yes_refuses_collateral_without_allow_collateral(tmp_path, capsys):
    db, ids = _db(tmp_path)

    assert _forget(db, ids["Denver"], "--yes") == 1

    assert "--allow-collateral" in capsys.readouterr().err
    assert _turn_exists(db)


def test_cli_openings_verify_the_commitments_and_are_never_stored(tmp_path, capsys):
    db, ids = _db(tmp_path)

    rc = _forget(db, ids["Denver"], "--yes", "--allow-collateral", "--emit-opening",
                 "--reason", "user asked")

    receipt = json.loads(capsys.readouterr().out)
    assert rc == 0 and receipt["status"] == "erased"
    texts = {"t1": TEXTS["t1"], ids["Denver"]: TEXTS["Denver"], ids["Dana"]: TEXTS["Dana"]}
    memory = AgentMemory(db)
    before = {r["memory_id"]: r["before_sha"] for r in memory.store.audit_log()}
    memory.close()
    assert {o["subject_id"] for o in receipt["openings"]} == set(texts)
    stored = db.read_bytes()
    for opening in receipt["openings"]:
        salt = bytes.fromhex(opening["salt"])
        text = texts[opening["subject_id"]].encode("utf-8")
        expected = hashlib.sha256(b"mneme.erase.v1\x00" + salt + text).hexdigest()
        assert before[opening["erase_ref"]] == expected
        assert salt not in stored and opening["salt"].encode() not in stored


def test_cli_missing_target_and_stale_plan_are_named(tmp_path, capsys):
    db, ids = _db(tmp_path)

    assert _forget(db, "no-such-id", "--yes") == 2
    assert "no memory" in capsys.readouterr().err
    assert _forget(db, ids["Denver"], "--yes", "--allow-collateral",
                   "--plan-sha256", "0" * 64) == 1
    assert "stale" in capsys.readouterr().err
    assert _turn_exists(db)


def test_cli_turn_and_session_targets(tmp_path, capsys):
    db, _ids = _db(tmp_path)

    assert _forget(db, "t1", "--turn", "--yes") == 0
    assert json.loads(capsys.readouterr().out)["counts"]["turns"] == 1
    assert _forget(db, "s", "--session", "--yes") == 0
    assert json.loads(capsys.readouterr().out)["counts"]["turns"] == 1
    memory = AgentMemory(db)
    assert memory.store.turns() == [] and memory.store.memories() == []
    memory.close()


def test_cli_without_yes_asks_and_a_no_deletes_nothing(tmp_path, capsys, monkeypatch):
    db, ids = _db(tmp_path)
    monkeypatch.setattr(cli_forget, "_interactive", lambda: False)
    assert _forget(db, ids["Denver"]) == 1
    assert "confirmation required" in capsys.readouterr().err

    monkeypatch.setattr(cli_forget, "_interactive", lambda: True)
    monkeypatch.setattr(cli_forget, "_ask", lambda prompt: "n")
    assert _forget(db, ids["Denver"]) == 1
    assert _turn_exists(db)
    monkeypatch.setattr(cli_forget, "_ask", lambda prompt: "y")
    assert _forget(db, ids["Denver"]) == 0
    assert not _turn_exists(db)


def test_cli_refuses_a_reason_that_repeats_erased_text(tmp_path, capsys):
    db, ids = _db(tmp_path)

    rc = _forget(db, ids["Denver"], "--yes", "--allow-collateral",
                 "--reason", "remove I live in Denver.")

    assert rc == 1 and "Denver" not in capsys.readouterr().err
    assert _turn_exists(db)


def test_cli_refuses_a_missing_database_without_creating_it(tmp_path, capsys):
    missing = tmp_path / "missing.db"

    assert _forget(missing, "any-id", "--yes") == 2
    assert main(["--state", str(missing), "scrub"]) == 2

    assert "no mneme database" in capsys.readouterr().err
    assert not missing.exists()


def test_cli_scrub_and_legacy_temp_snapshot_paths(tmp_path, capsys, monkeypatch):
    import tempfile

    legacy_dir = tmp_path / "os-temp"
    legacy_dir.mkdir()
    (legacy_dir / "mneme-replay-old.db").write_bytes(b"\x00" * 32)
    monkeypatch.setattr(tempfile, "tempdir", str(legacy_dir))
    db, _ids = _db(tmp_path)

    assert _forget(db, "t1", "--turn", "--yes") == 0
    receipt = json.loads(capsys.readouterr().out)
    assert main(["--state", str(db), "scrub"]) == 0
    scrub = json.loads(capsys.readouterr().out)

    assert receipt["legacy_temp_snapshot_paths"] == [str(legacy_dir / "mneme-replay-old.db")]
    assert scrub["vacuum"] == "done" and scrub["freelist_count"] == 0


def _mcp(arguments):
    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "mneme.forget", "arguments": arguments}})
    return response["result"]


@pytest.fixture
def mcp_state(tmp_path, monkeypatch):
    db, ids = _db(tmp_path)
    monkeypatch.setenv("MNEME_STATE", str(db))
    return db, ids


def test_mcp_memory_id_alone_returns_a_plan_and_deletes_nothing(mcp_state):
    db, ids = mcp_state

    result = _mcp({"memory_id": ids["Denver"]})

    assert result["isError"] is False
    plan = json.loads(result["content"][0]["text"])
    assert plan["schema"] == "mneme.erase-plan/1"
    assert plan["collateral"] == [ids["Dana"]]
    assert "Dana" not in result["content"][0]["text"]
    assert _turn_exists(db)


def test_mcp_previews_only_on_request(mcp_state):
    _db_path, ids = mcp_state

    plan = json.loads(_mcp({"memory_id": ids["Denver"], "include_previews": True})
                      ["content"][0]["text"])

    assert plan["previews"][ids["Dana"]] == TEXTS["Dana"]


def test_mcp_confirm_applies_exactly_that_plan_and_returns_no_opening(mcp_state):
    db, ids = mcp_state
    plan = json.loads(_mcp({"memory_id": ids["Denver"]})["content"][0]["text"])

    result = _mcp({"memory_id": ids["Denver"], "reason": "user asked",
                   "confirm_plan_sha256": plan["plan_sha256"]})

    assert result["isError"] is False
    receipt = json.loads(result["content"][0]["text"])
    assert receipt["counts"]["collateral"] == 1
    assert "openings" not in receipt and '"salt"' not in result["content"][0]["text"]
    assert not _turn_exists(db)


def test_mcp_stale_digest_unknown_argument_and_missing_memory_are_refused(mcp_state):
    db, ids = mcp_state

    stale = _mcp({"memory_id": ids["Denver"], "confirm_plan_sha256": "0" * 64})
    unknown = _mcp({"memory_id": ids["Denver"], "force": True})
    missing = _mcp({"memory_id": "no-such-id"})

    assert stale["isError"] is True and "stale" in stale["content"][0]["text"]
    assert unknown["isError"] is True and "unknown argument" in unknown["content"][0]["text"]
    assert missing["isError"] is True and "no memory" in missing["content"][0]["text"]
    assert _turn_exists(db)
