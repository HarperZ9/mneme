"""Falsifiers for what the forget surfaces reveal and change.

A plan printed to a pipe can reach an agent's model context, so the CLI shows
row text only on a terminal or with --show-text. A dry run changes nothing,
even on a database an older mneme wrote. The MCP plan names no tenant, MCP
apply needs explicit consent for collateral, and the MCP doctor keeps the
owner's home directory out of its paths. Every text is planted test data.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import mneme.cli_forget as cli_forget
from mneme import AgentMemory
from mneme.cli import main
from mneme.erase import Selection, StalePlanError, apply_erase, plan_erase
from mneme.mcp import handle_request

TWO_FACTS = [{"id": "t1", "role": "user", "text": "My name is Dana. I live in Denver."},
             {"id": "t2", "role": "user", "text": "I prefer dark roast coffee."}]


def _db(tmp_path, user: str = ""):
    db = tmp_path / "mneme.db"
    memory = AgentMemory(db)
    memory.remember("s", TWO_FACTS, user=user)
    ids = {n: next(r["id"] for r in memory.store.memories(layer="L1") if n in r["text"])
           for n in ("Denver", "Dana")}
    memory.close()
    return db, ids


def test_a_piped_plan_carries_no_row_text_unless_asked(tmp_path, capsys):
    db, ids = _db(tmp_path)

    assert main(["--state", str(db), "forget", ids["Denver"], "--dry-run"]) == 0
    piped = capsys.readouterr().out
    assert main(["--state", str(db), "forget", ids["Denver"], "--dry-run",
                 "--show-text"]) == 0
    shown = json.loads(capsys.readouterr().out)

    assert "Dana" not in piped and "previews" not in json.loads(piped)
    assert shown["previews"][ids["Dana"]] == "My name is Dana."


def test_end_of_input_at_the_prompt_refuses_and_deletes_nothing(tmp_path, capsys, monkeypatch):
    db, ids = _db(tmp_path)

    def closed(_prompt):
        raise EOFError

    monkeypatch.setattr(cli_forget, "_interactive", lambda: True)
    monkeypatch.setattr(cli_forget, "_ask", closed)
    assert main(["--state", str(db), "forget", ids["Denver"]]) == 1

    assert "confirmation" in capsys.readouterr().err
    memory = AgentMemory(db)
    assert memory.store.turn("t1") is not None
    memory.close()


def test_a_dry_run_leaves_a_legacy_database_byte_for_byte(legacy_042_db, capsys):
    before = hashlib.sha256(legacy_042_db.read_bytes()).hexdigest()

    rc = main(["--state", str(legacy_042_db), "forget", "t1", "--turn", "--dry-run"])

    assert rc == 0 and json.loads(capsys.readouterr().out)["turns"] == ["t1"]
    assert hashlib.sha256(legacy_042_db.read_bytes()).hexdigest() == before


def test_the_plan_digest_binds_row_content(tmp_path):
    db, ids = _db(tmp_path)
    memory = AgentMemory(db)
    selection = Selection(memories=(ids["Denver"],))
    plan = plan_erase(memory.store, selection)

    memory.update(ids["Dana"], "My name is Dana Q.", reason="typo")

    with pytest.raises(StalePlanError):
        apply_erase(memory.store, selection, plan["plan_sha256"])
    memory.close()


def _mcp(arguments):
    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "mneme.forget", "arguments": arguments}})
    return response["result"]


def test_the_mcp_plan_names_no_tenant(tmp_path, monkeypatch):
    db, ids = _db(tmp_path, user="dana@example.test")
    monkeypatch.setenv("MNEME_STATE", str(db))

    text = _mcp({"memory_id": ids["Denver"]})["content"][0]["text"]

    assert "dana@example.test" not in text
    assert json.loads(text)["counts"]["users"] == 1


def test_mcp_apply_needs_explicit_consent_for_collateral(tmp_path, monkeypatch):
    db, ids = _db(tmp_path)
    monkeypatch.setenv("MNEME_STATE", str(db))
    plan = json.loads(_mcp({"memory_id": ids["Denver"]})["content"][0]["text"])
    confirm = {"memory_id": ids["Denver"], "confirm_plan_sha256": plan["plan_sha256"]}

    refused = _mcp(confirm)
    applied = _mcp({**confirm, "allow_collateral": True})

    assert refused["isError"] is True and "allow_collateral" in refused["content"][0]["text"]
    assert applied["isError"] is False
    assert json.loads(applied["content"][0]["text"])["counts"]["collateral"] == 1


def test_the_mcp_doctor_writes_the_home_directory_as_a_tilde(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("MNEME_STATE", str(tmp_path / "work" / "mneme.db"))

    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "mneme.doctor", "arguments": {}}})
    info = json.loads(response["result"]["content"][0]["text"])

    assert not [v for v in _strings(info) if str(tmp_path) in v]
    assert info["state_path_absolute"].startswith("~")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
