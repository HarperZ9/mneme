"""Falsifiers for what the forget surfaces print, and to where.

An opening (salt plus content-derived id) turns the hiding commitment back
into a way to confirm a guess, so `--emit-opening` writes only to a terminal
and is refused, before anything is deleted, when stdout is a pipe. A piped
plan carries no tenant names. The receipt carries no plan digest, which a
guessed text would rebuild, and no local paths; the paths of legacy temp
snapshots go to stderr. The MCP doctor writes the home directory as `~` in
any letter case or slash direction. Every value is planted test data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import mneme.cli_forget as cli_forget
import mneme.erase_receipt as erase_receipt
from mneme import AgentMemory
from mneme.cli import main
from mneme.state_report import mcp_doctor

TURNS = [{"id": "t1", "role": "user", "text": "My name is Dana. I live in Denver."},
         {"id": "t2", "role": "user", "text": "I prefer dark roast coffee."}]


def _db(tmp_path, user: str = ""):
    db = tmp_path / "mneme.db"
    memory = AgentMemory(db)
    memory.remember("s", TURNS, user=user)
    atom = next(r["id"] for r in memory.store.memories(layer="L1") if "Denver" in r["text"])
    memory.close()
    return db, atom


def _forget(db, *args):
    return main(["--state", str(db), "forget", *args])


def _rows(db) -> int:
    memory = AgentMemory(db)
    try:
        return len(memory.store.turns())
    finally:
        memory.close()


def test_emit_opening_is_refused_on_a_pipe_before_anything_is_deleted(tmp_path, capsys):
    db, atom = _db(tmp_path)

    rc = _forget(db, atom, "--yes", "--allow-collateral", "--emit-opening")

    captured = capsys.readouterr()
    assert rc == 1
    assert "terminal" in captured.err
    assert '"salt"' not in captured.out
    assert _rows(db) == 2


def test_emit_opening_writes_on_a_terminal(tmp_path, capsys, monkeypatch):
    db, atom = _db(tmp_path)
    monkeypatch.setattr(cli_forget, "_stdout_is_terminal", lambda: True)

    rc = _forget(db, atom, "--yes", "--allow-collateral", "--emit-opening")

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["openings"]


def test_a_piped_plan_names_no_tenant(tmp_path, capsys):
    db, atom = _db(tmp_path, user="planted-tenant-name")

    assert _forget(db, atom, "--dry-run") == 0

    out = capsys.readouterr().out
    assert "planted-tenant-name" not in out
    assert json.loads(out)["counts"]["users"] == 1


def test_the_receipt_carries_no_plan_digest_and_no_path(tmp_path, capsys):
    import tempfile

    legacy = Path(tempfile.gettempdir()) / "mneme-replay-1-old.db"
    legacy.write_bytes(b"\x00" * 32)
    db, atom = _db(tmp_path)
    plan = json.loads(_dry(db, atom, capsys))

    rc = _forget(db, atom, "--yes", "--allow-collateral")

    captured = capsys.readouterr()
    assert rc in (0, 3)
    assert plan["plan_sha256"] not in captured.out
    assert "plan_sha256" not in json.loads(captured.out)
    assert str(legacy) not in captured.out and str(legacy) in captured.err


def _dry(db, atom, capsys) -> str:
    assert _forget(db, atom, "--dry-run") == 0
    return capsys.readouterr().out


def test_the_unverified_receipt_names_what_is_out_of_reach(tmp_path, monkeypatch, capsys):
    import sqlite3

    db, atom = _db(tmp_path)

    def locked(_conn):
        raise sqlite3.OperationalError("planted: database is locked")

    monkeypatch.setattr(erase_receipt, "kept_values", locked)
    assert _forget(db, atom, "--yes", "--allow-collateral") == 3

    receipt = json.loads(capsys.readouterr().out)
    classes = {item["class"] for item in receipt["out_of_reach"]}
    assert receipt["status"] == "erased_unverified" and "plan_sha256" not in receipt
    assert {"exports", "copies_and_backups", "model_providers",
            "client_transcripts"} <= classes


def test_the_receipt_names_the_mcp_clients_own_history(tmp_path):
    db, atom = _db(tmp_path)
    memory = AgentMemory(db)

    receipt = memory.forget(atom, reason="user asked", allow_collateral=True)
    memory.close()

    client = next(i for i in receipt["out_of_reach"] if i["class"] == "client_transcripts")
    assert "session history" in client["note"]


@pytest.mark.skipif(os.name != "nt", reason="Windows paths ignore case and slash direction")
def test_the_mcp_doctor_writes_home_as_tilde_in_any_case_or_slash(monkeypatch):
    home = Path(r"C:\Users\PlantedOwner")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    for state in ("C:/Users/PlantedOwner/proj/mneme.db",
                  r"c:\users\plantedowner\proj\mneme.db"):
        report = mcp_doctor(state)
        text = json.dumps(report).lower()
        assert "plantedowner" not in text, state
        assert report["state_path"].startswith("~")


def test_the_mcp_doctor_writes_home_as_tilde(monkeypatch, tmp_path):
    home = tmp_path / "planted-home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    report = mcp_doctor(str(home / "proj" / "mneme.db"))

    assert "planted-home" not in json.dumps(report)
