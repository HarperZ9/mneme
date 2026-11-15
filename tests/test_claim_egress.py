"""The locality claim: every public operation runs with the network denied.

The store stays on your machine; text a caller sends through the optional LLM
extractor goes to that model's provider, and that path is off unless a caller
injects it. A Python audit hook is armed around each call. While armed it records, and
refuses, every socket creation, connect, bind, send and name lookup, every
urllib and http.client request, and every started process (the client privacy
text says the server "starts no other program"). The hook sees the C-level
socket events, so a code path that reached the network through any standard
library module is caught.

Three tables list the public surface: every public method of `AgentMemory`,
every CLI subcommand and every MCP tool. Each table must equal the surface
the code exposes, so a new operation cannot ship without an entry here.

The planted controls stay in the suite: an operation that does connect (the
optional `LLMExtractor`, which is off unless a caller injects it, and a bare
socket) must be recorded by the same hook.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import socket
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from mneme import AgentMemory, cli, mcp
from mneme.extract import RuleExtractor
from mneme.llm_extract import LLMExtractor
from test_crucible_replay import _state as replay_state, _template as replay_template
from test_ingest import GATHER_ITEMS

SRC = Path(__file__).resolve().parents[1] / "src" / "mneme"
NETWORK_EVENTS = ("socket.", "urllib.Request", "http.client.", "ftplib.", "smtplib.",
                  "imaplib.", "poplib.", "nntplib.", "telnetlib.", "webbrowser.open")
PROCESS_EVENTS = ("subprocess.Popen", "os.system", "os.exec", "os.spawn",
                  "os.posix_spawn", "os.startfile")
NETWORK_MODULES = {"socket", "ssl", "http", "urllib.request", "ftplib", "smtplib",
                   "imaplib", "poplib", "telnetlib", "xmlrpc", "asyncio", "requests",
                   "httpx", "aiohttp", "websocket", "websockets", "webbrowser"}
_STATE = {"armed": False, "events": []}


class EgressAttempt(Exception):
    """An operation tried to reach the network or start a process."""


def _hook(event: str, _args) -> None:
    if _STATE["armed"] and event.startswith(NETWORK_EVENTS + PROCESS_EVENTS):
        _STATE["events"].append(event)
        raise EgressAttempt(event)


sys.addaudithook(_hook)


@contextmanager
def network_denied():
    """Arm the hook. Yields the list of refused events, empty when none happened."""
    _STATE["events"] = events = []
    _STATE["armed"] = True
    try:
        yield events
    finally:
        _STATE["armed"] = False


def _seeded(db: Path) -> tuple[AgentMemory, str]:
    memory = AgentMemory(db, embed="ngram")
    memory.remember("home", [{"role": "user", "text": "I live in Denver near the park."},
                             {"role": "user", "text": "I prefer green tea daily."}],
                    user="alice")
    memory.remember("work", [{"role": "user", "text": "I always use a standing desk."}],
                    user="bob")
    target = next(r["id"] for r in memory.store.memories() if "Denver" in r["text"])
    return memory, target


# -- the library ---------------------------------------------------------------

def _library_calls(tmp_path: Path) -> dict:
    """One call per public AgentMemory method. Each gets (memory, memory_id)."""
    replay_db = tmp_path / "replay.db"
    return {
        "remember": lambda m, t: m.remember("s9", [{"role": "user", "text": "I like maps."}]),
        "recall": lambda m, t: m.recall("Denver", strategy="hybrid", recency_weight=0.2),
        "navigate": lambda m, t: m.navigate("Denver"),
        "drift": lambda m, t: m.drift(),
        "provenance": lambda m, t: m.provenance(t),
        "provenance_chain": lambda m, t: m.provenance_chain(t),
        "ingest_gather": lambda m, t: m.ingest_gather("research", GATHER_ITEMS),
        "recheck_local_origin": lambda m, t: m.recheck_local_origin(t, allowed_root=tmp_path),
        "to_crucible": lambda m, t: m.to_crucible("home"),
        "replay_crucible": lambda m, t: _replay(replay_db),
        "consolidate": lambda m, t: m.consolidate("home"),
        "entity_graph": lambda m, t: m.entity_graph(),
        "build_scenarios": lambda m, t: m.build_scenarios("home", user="alice"),
        "persona": lambda m, t: m.persona("home", user="alice"),
        "update": lambda m, t: m.update(t, "I live in Denver near the big park."),
        "supersede": lambda m, t: m.supersede(t, "I live in Boulder now."),
        "history": lambda m, t: m.history(contains="live"),
        "audit": lambda m, t: m.audit(),
        "forget": lambda m, t: m.forget(t, reason="asked", allow_collateral=True),
        "forget_subject": lambda m, t: m.forget_subject("bob", reason="asked"),
        "verify_subject_erasure": lambda m, t: m.verify_subject_erasure({}),
        "close": lambda m, t: m.close(),
    }


def _replay(db: Path) -> dict:
    writer = replay_state(db)
    template = replay_template(writer)
    writer.close()
    reader = AgentMemory(db, read_only=True, immutable_snapshot=True)
    try:
        return reader.replay_crucible(template)
    finally:
        reader.close()


def test_every_public_library_method_runs_with_the_network_denied(tmp_path):
    calls = _library_calls(tmp_path)
    public = {name for name in dir(AgentMemory) if not name.startswith("_")}
    assert set(calls) == public, "a public AgentMemory method has no egress coverage"
    with network_denied() as events:
        memory, target = _seeded(tmp_path / "mem.db")
        results = {name: call(memory, target) for name, call in calls.items()
                   if name != "close"}
        calls["close"](memory, target)
    assert events == []
    assert results["forget"]["schema"] == "mneme.erase-receipt/1"
    assert results["forget_subject"]["schema"] == "mneme.subject-erasure-receipt/1"
    assert results["replay_crucible"]["schema"] == "crucible.replay-pack/1"
    assert results["recall"].as_dict()["hits"]


def test_the_default_extractor_and_embedder_are_local(tmp_path):
    memory = AgentMemory(tmp_path / "mem.db")
    assert isinstance(memory.extractor, RuleExtractor)
    assert memory.embedder is None
    assert AgentMemory(embed="ngram").embedder.name.startswith("ngram(")
    memory.close()


# -- the command line ----------------------------------------------------------

def _cli_commands(tmp_path: Path, target: str) -> dict:
    turns, items = tmp_path / "turns.json", tmp_path / "items.json"
    turns.write_text(json.dumps([{"role": "user", "text": "I work in Austin."}]), "utf-8")
    items.write_text(json.dumps(GATHER_ITEMS), "utf-8")
    receipt = tmp_path / "subject-receipt.json"
    receipt.write_text("{}", "utf-8")
    template = tmp_path / "template.json"
    writer = replay_state(tmp_path / "replay.db")
    template.write_text(json.dumps(replay_template(writer)), "utf-8")
    writer.close()
    return {
        "remember": ["remember", "s3", str(turns), "--user", "alice"],
        "recall": ["recall", "Denver", "--embed", "ngram", "--json"],
        "drift": ["drift"],
        "persona": ["persona", "home", "--user", "alice"],
        "provenance": ["provenance", target],
        "scenarios": ["scenarios", "home", "--user", "alice"],
        "update": ["update", target, "I live in Denver near the old park."],
        "audit": ["audit"],
        "inspect": ["inspect", "--out", str(tmp_path / "page.html")],
        "ingest": ["ingest", "research", str(items)],
        "chain": ["chain", target],
        "origin-recheck": ["origin-recheck", target, "--allowed-root", str(tmp_path)],
        "supersede": ["supersede", target, "I live in Boulder now."],
        "history": ["history", "--contains", "live"],
        "entity-graph": ["entity-graph"],
        "consolidate": ["consolidate", "--session", "home"],
        "to-crucible": ["to-crucible", "--session", "home"],
        "replay-crucible": ["--state", str(tmp_path / "replay.db"), "replay-crucible",
                            str(template), "--out", str(tmp_path / "pack.json")],
        "bench": ["bench"],
        "status": ["status"],
        "doctor": ["doctor"],
        "forget": ["forget", target, "--yes", "--allow-collateral", "--reason", "asked"],
        "scrub": ["scrub"],
        "forget-subject": ["forget-subject", "bob", "--yes", "--reason", "asked"],
        "verify-erasure": ["verify-erasure", str(receipt)],
        "mcp": ["mcp"],
    }


# 1 for origin-recheck: the target has no local-file origin to recheck.
# 1 for verify-erasure: the receipt it is given is empty, so the checks fail.
EXPECTED_EXIT_CODES = {
    "remember": 0, "recall": 0, "drift": 0, "persona": 0, "provenance": 0,
    "scenarios": 0, "update": 0, "audit": 0, "inspect": 0, "ingest": 0, "chain": 0,
    "origin-recheck": 1, "supersede": 0, "history": 0, "entity-graph": 0,
    "consolidate": 0, "to-crucible": 0, "replay-crucible": 0, "bench": 0, "status": 0,
    "doctor": 0, "forget": 0, "scrub": 0, "forget-subject": 0, "verify-erasure": 1,
    "mcp": 0,
}


def _subcommands() -> set[str]:
    for action in cli.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("the CLI has no subcommands")


def test_every_cli_command_runs_with_the_network_denied(tmp_path, monkeypatch, capsys):
    db = tmp_path / "mem.db"
    memory, target = _seeded(db)
    memory.close()
    commands = _cli_commands(tmp_path, target)
    assert set(commands) == _subcommands(), "a CLI command has no egress coverage"
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"))
    codes = {}
    with network_denied() as events:
        for name, argv in commands.items():
            full = argv if argv[0] == "--state" else ["--state", str(db), *argv]
            codes[name] = cli.main(full)
    capsys.readouterr()
    assert events == []
    assert codes == EXPECTED_EXIT_CODES


# -- the MCP server ------------------------------------------------------------

def _mcp_arguments(tmp_path: Path, target: str, template: dict) -> dict:
    return {
        "mneme.remember": {"session": "s4", "turns": [{"role": "user",
                                                       "text": "I like quiet rooms."}]},
        "mneme.recall": {"query": "Denver", "strategy": "hybrid"},
        "mneme.drift": {},
        "mneme.to_crucible": {"all_users": True},
        "mneme.replay_crucible": {"template": template},
        "mneme.provenance": {"memory_id": target},
        "mneme.origin_recheck": {"memory_id": target, "allowed_root": str(tmp_path)},
        "mneme.forget": {"memory_id": target},
        "mneme.audit": {},
        "mneme.status": {},
        "mneme.doctor": {},
    }


def test_every_mcp_tool_runs_with_the_network_denied(tmp_path, monkeypatch):
    db = tmp_path / "mem.db"
    memory, target = _seeded(db)
    memory.close()
    writer = replay_state(tmp_path / "replay.db")
    template = replay_template(writer)
    writer.close()
    arguments = _mcp_arguments(tmp_path, target, template)
    assert set(arguments) == {t["name"] for t in mcp._tool_defs()}, \
        "an MCP tool has no egress coverage"
    monkeypatch.setenv("MNEME_STATE", str(db))
    with network_denied() as events:
        for name, args in arguments.items():
            if name == "mneme.replay_crucible":
                monkeypatch.setenv("MNEME_STATE", str(tmp_path / "replay.db"))
            response = mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                           "params": {"name": name, "arguments": args}})
            monkeypatch.setenv("MNEME_STATE", str(db))
            assert response["result"]["isError"] is False, (name, response)
        plan = json.loads(mcp.call_tool("mneme.forget", {"memory_id": target}))
        applied = json.loads(mcp.call_tool("mneme.forget", {
            "memory_id": target, "confirm_plan_sha256": plan["plan_sha256"],
            "allow_collateral": True}))
        assert applied["schema"] == "mneme.erase-receipt/1"
        out = io.StringIO()
        mcp.serve(io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 2,
                                          "method": "initialize"}) + "\n"), out)
    assert events == []


# -- nothing but the optional extractor can reach the network -------------------

def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(("." * node.level) + node.module)
            found |= {f"{'.' * node.level}{node.module}.{alias.name}" for alias in node.names}
    return found


def test_only_the_optional_extractor_imports_a_network_module():
    importers, wired = set(), set()
    for path in sorted(SRC.glob("*.py")):
        names = _imports(path)
        if any(n == m or n.startswith(m + ".") for n in names for m in NETWORK_MODULES):
            importers.add(path.name)
        if path.name != "llm_extract.py" and any("llm_extract" in n for n in names):
            wired.add(path.name)
    assert importers == {"llm_extract.py"}
    assert wired == set(), "the optional extractor must stay off unless a caller injects it"


# -- planted controls: a real connection attempt must be recorded --------------

def test_control_the_optional_llm_extractor_is_caught_when_injected(tmp_path):
    memory = AgentMemory(tmp_path / "mem.db",
                         extractor=LLMExtractor(base_url="http://127.0.0.1:9/v1"))
    with network_denied() as events:
        with pytest.raises(EgressAttempt):
            memory.remember("s", [{"role": "user", "text": "I live in Denver."}])
    memory.close()
    assert events and events[0].startswith(("urllib.Request", "socket."))


def test_control_a_bare_socket_and_a_started_process_are_caught():
    with network_denied() as events:
        with pytest.raises(EgressAttempt):
            socket.create_connection(("127.0.0.1", 9), timeout=0.2)
        with pytest.raises(EgressAttempt):
            socket.getaddrinfo("example.invalid", 443)
        with pytest.raises(EgressAttempt):
            import subprocess
            subprocess.Popen([sys.executable, "-c", "pass"])
    assert [e.split(".")[0] for e in events] == ["socket", "socket", "subprocess"]
    assert _STATE["armed"] is False
