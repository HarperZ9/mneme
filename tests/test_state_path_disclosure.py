"""Falsifiers for state path disclosure.

The default database is `mneme.db` in whatever directory a command runs, so an
owner can end up with memory databases scattered across project folders, some
inside git work trees where one `git add .` publishes them. `mneme status` and
`mneme doctor` print where the database really is and where its replay
snapshots go, never create or change the database, and warn about a git work
tree. The MCP doctor reports the same path facts without opening the database.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import mneme.state_report as state_report
from mneme import AgentMemory
from mneme.cli import main
from mneme.mcp import handle_request


def _db(path):
    memory = AgentMemory(path)
    memory.remember("s", [{"id": "t1", "role": "user", "text": "I live in Denver."}])
    memory.close()
    return path


def _run(capsys, *argv):
    rc = main(list(argv))
    return rc, json.loads(capsys.readouterr().out)


def _git_repo(root):
    (root / ".git" / "objects").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return root


def _home_as_tilde(path: str) -> str:
    """The MCP doctor writes the home directory as `~` (its result reaches a model)."""
    return path.replace(str(Path.home()), "~")


def _mcp_doctor() -> dict:
    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "mneme.doctor", "arguments": {}}})
    return json.loads(response["result"]["content"][0]["text"])


def test_status_prints_the_absolute_path_and_the_snapshot_directory(
        tmp_path, monkeypatch, capsys, snapshot_root):
    _db(tmp_path / "mneme.db")
    monkeypatch.chdir(tmp_path)

    rc, status = _run(capsys, "status")                   # the default --state

    assert rc == 0
    assert status["state_path"] == str((tmp_path / "mneme.db").resolve())
    assert status["default_location"] is True and status["notes"]
    assert status["snapshot_dir"] == str(snapshot_root)
    assert status["store_snapshot_dir"] == str(snapshot_root / status["store_id"])
    assert status["counts"]["turns"] == 1 and status["counts"]["memories"] == {"L1": 1}
    assert status["files"][0]["name"] == "mneme.db" and status["files"][0]["bytes"] > 0


def test_status_and_doctor_never_change_the_database(tmp_path, capsys):
    db = _db(tmp_path / "mneme.db")
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    _run(capsys, "--state", str(db), "status")
    _run(capsys, "--state", str(db), "doctor")

    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mneme.db"]


def test_status_never_creates_a_missing_database(tmp_path, capsys):
    missing = tmp_path / "nowhere" / "mneme.db"

    rc, status = _run(capsys, "--state", str(missing), "status")

    assert rc == 0 and status["exists"] is False
    assert any("creates it here" in w for w in status["warnings"])
    assert not missing.exists() and not missing.parent.exists()


def test_doctor_warns_when_the_database_is_inside_a_git_work_tree(tmp_path, capsys):
    repo = _git_repo(tmp_path / "project")
    (repo / "notes").mkdir()
    inside = _db(repo / "notes" / "mneme.db")
    outside = _db(tmp_path / "private.db")

    rc_in, doctor_in = _run(capsys, "--state", str(inside), "doctor")
    rc_out, doctor_out = _run(capsys, "--state", str(outside), "doctor")

    assert rc_in == 1 and doctor_in["git_work_tree"] == str(repo)
    assert any("git work tree" in w and str(repo) in w for w in doctor_in["warnings"])
    assert rc_out == 0 and doctor_out["warnings"] == []
    assert doctor_out["audit"]["chain_intact"] is True


def test_a_gitdir_file_marks_a_work_tree_and_a_bare_git_folder_does_not(tmp_path, capsys):
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text("gitdir: ../repo/.git/worktrees/linked\n", encoding="utf-8")
    stray = tmp_path / "stray"
    (stray / ".git" / "info").mkdir(parents=True)     # a folder, but no repository

    _rc, in_linked = _run(capsys, "--state", str(_db(linked / "mneme.db")), "status")
    _rc, in_stray = _run(capsys, "--state", str(_db(stray / "mneme.db")), "status")

    assert in_linked["git_work_tree"] == str(linked)
    assert in_stray["git_work_tree"] is None and in_stray["warnings"] == []


def test_doctor_reports_a_schema_downgrade_and_a_broken_audit_chain(tmp_path, capsys):
    db = _db(tmp_path / "mneme.db")
    memory = AgentMemory(db)
    memory.forget(memory.store.memories(layer="L1")[0]["id"], reason="user asked")
    memory.close()
    conn = sqlite3.connect(db)
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_downgrade_seen','3')")
    conn.execute("UPDATE audit SET reason='forged'")
    conn.commit()
    conn.close()

    rc, doctor = _run(capsys, "--state", str(db), "doctor")

    assert rc == 1 and doctor["audit"]["chain_intact"] is False
    assert any("older mneme" in w for w in doctor["warnings"])
    assert any("audit chain" in w for w in doctor["warnings"])


def test_doctor_counts_orphaned_and_legacy_snapshots(tmp_path, capsys, snapshot_root):
    db = _db(tmp_path / "mneme.db")
    _rc, status = _run(capsys, "--state", str(db), "status")
    store_dir = snapshot_root / status["store_id"]
    store_dir.mkdir(parents=True)
    child = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                           capture_output=True, text=True, check=True)
    (store_dir / f"mneme-replay-{int(child.stdout)}-abc.db").write_bytes(b"x")
    legacy = tempfile.gettempdir()
    (Path(legacy) / "mneme-replay-old.db").write_bytes(b"x")

    rc, doctor = _run(capsys, "--state", str(db), "doctor")

    assert rc == 1
    assert doctor["snapshots"] == {"live": 0, "orphaned": 1, "unknown": 0, "legacy_temp": 1}
    assert any("gone" in w and str(store_dir) in w for w in doctor["warnings"])
    assert any("before 0.5.0" in w and legacy in w for w in doctor["warnings"])


@pytest.mark.parametrize("state, phrase", [(":memory:", "nothing is kept"),
                                           ("", "temporary database")])
def test_a_state_that_keeps_nothing_is_named(state, phrase, capsys):
    rc, doctor = _run(capsys, "--state", state, "doctor")

    assert rc == 1 and doctor["state_path"] is None
    assert any(phrase in w for w in doctor["warnings"])


def test_mcp_doctor_discloses_the_resolved_path_and_the_snapshot_directory(
        tmp_path, monkeypatch, snapshot_root):
    db = _db(tmp_path / "mneme.db")
    monkeypatch.setenv("MNEME_STATE", str(db))

    body = _mcp_doctor()

    assert body["ok"] is True and body["state_from_env"] is True
    assert body["state_path"] == _home_as_tilde(str(db))
    assert body["state_path_absolute"] == _home_as_tilde(str(db.resolve()))
    assert body["snapshot_dir"] == _home_as_tilde(str(snapshot_root))
    assert body["exists"] is True and body["warnings"] == []
    assert "mneme.forget" in body["tools"]


def test_mcp_doctor_never_opens_the_database(tmp_path, monkeypatch):
    db = _db(tmp_path / "mneme.db")
    monkeypatch.setenv("MNEME_STATE", str(db))

    def refuse(*_args, **_kwargs):
        raise AssertionError("the MCP doctor opened the database")

    monkeypatch.setattr(state_report.sqlite3, "connect", refuse)
    monkeypatch.setattr(sqlite3, "connect", refuse)

    body = _mcp_doctor()

    assert body["ok"] is True and body["exists"] is True


def test_mcp_doctor_names_the_default_location_and_a_missing_database(
        tmp_path, monkeypatch):
    monkeypatch.delenv("MNEME_STATE", raising=False)
    monkeypatch.chdir(tmp_path)

    body = _mcp_doctor()

    assert body["state_from_env"] is False and body["default_location"] is True
    assert body["state_path_absolute"] == _home_as_tilde(
        str((tmp_path / "mneme.db").resolve()))
    assert any("creates it here" in w for w in body["warnings"])
    assert not (tmp_path / "mneme.db").exists()


def test_status_on_a_wal_database_leaves_its_rows_and_main_file_unchanged(tmp_path, capsys):
    db = tmp_path / "wal.db"
    memory = AgentMemory(db)
    memory.store.conn.execute("PRAGMA journal_mode=WAL")
    memory.remember("s", [{"id": "t1", "role": "user", "text": "I live in Denver."}])
    memory.close()
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    _run(capsys, "--state", str(db), "status")
    _run(capsys, "--state", str(db), "doctor")

    # a WAL reader may create the -wal and -shm files, as the README says
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert {p.name for p in tmp_path.iterdir()} <= {"wal.db", "wal.db-wal", "wal.db-shm"}
