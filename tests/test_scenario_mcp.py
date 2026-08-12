"""Falsifiers for L2 scenarios and the MCP server — the feature-parity layer.

L2: atoms cluster deterministically into scene blocks, each citing its atoms so
it stays drift-checkable. MCP: the agent-facing tools work over stdio JSON-RPC
and a recall through MCP carries the same re-derivable receipt.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory
from mneme.mcp import handle_request

TURNS = [
    {"id": "t1", "role": "user", "text": "I love hiking in the mountains every weekend."},
    {"id": "t2", "role": "user", "text": "My favorite mountains are the Rockies for hiking."},
    {"id": "t3", "role": "user", "text": "I also collect vintage cameras from the 1970s."},
]


def _mem():
    m = AgentMemory(":memory:")
    m.remember("s", TURNS)
    return m


def test_scenarios_cluster_related_atoms_deterministically():
    m = _mem()
    out = m.build_scenarios("s")
    # the two hiking/mountain atoms cluster; the camera atom stands alone
    sizes = sorted(b["atoms"] for b in out["blocks"])
    assert sizes == [1, 2]
    # deterministic: a second identical build yields the same scenario ids
    m2 = _mem()
    out2 = m2.build_scenarios("s")
    assert [b["scenario_id"] for b in out["blocks"]] == [b["scenario_id"] for b in out2["blocks"]]


def test_scenario_cites_its_atoms_and_is_drift_checkable():
    m = _mem()
    m.build_scenarios("s")
    l2 = m.store.memories(layer="L2")
    assert l2
    prov = m.provenance(l2[0]["id"])
    assert prov["layer"] == "L2"
    assert all(sid.isalnum() for sid in prov["source_ids"])   # cites atom ids
    # deleting a cited atom makes the scenario UNVERIFIABLE (grounding gone)
    m.store.conn.execute("DELETE FROM memories WHERE layer='L1' AND id=?",
                         (prov["source_ids"][0],))
    m.store.conn.commit()
    report = m.drift(layer="L2")
    assert report["overall"] in ("DRIFT", "UNVERIFIABLE")


def _rpc(method, params=None, mid=1):
    return handle_request({"jsonrpc": "2.0", "id": mid, "method": method,
                           "params": params or {}})


def test_mcp_initialize_and_tools_list():
    init = _rpc("initialize")
    assert init["result"]["serverInfo"]["name"] == "mneme"
    tools = {t["name"] for t in _rpc("tools/list")["result"]["tools"]}
    assert tools == {"mneme.remember", "mneme.recall", "mneme.drift",
                     "mneme.provenance", "mneme.forget", "mneme.audit"}


def test_mcp_remember_then_recall_carries_the_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEME_STATE", str(tmp_path / "mcp.db"))
    r = _rpc("tools/call", {"name": "mneme.remember",
                            "arguments": {"session": "s", "turns": TURNS}})
    assert not r["result"].get("isError")
    summary = json.loads(r["result"]["content"][0]["text"])
    assert summary["atoms"] == 3
    rec = _rpc("tools/call", {"name": "mneme.recall",
                              "arguments": {"query": "hiking mountains", "strategy": "keyword"}})
    receipt = json.loads(rec["result"]["content"][0]["text"])
    assert receipt["schema"] == "mneme.recall/1"
    assert receipt["hits"] and "recheck" in receipt     # the re-derivable receipt rides through MCP


def test_mcp_unknown_tool_is_a_protocol_error():
    r = _rpc("tools/call", {"name": "mneme.nope", "arguments": {}})
    assert "error" in r and r["error"]["code"] == -32602


def test_mcp_tool_error_rides_the_result_not_the_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEME_STATE", str(tmp_path / "e.db"))
    monkeypatch.setenv("MNEME_TENANT", "tenant")
    r = _rpc("tools/call", {"name": "mneme.provenance",
                            "arguments": {"memory_id": "does-not-exist"}})
    assert r["result"]["isError"] is True               # not a JSON-RPC error frame
    assert "memory not found" in r["result"]["content"][0]["text"]


def test_mcp_object_access_uses_the_configured_tenant(tmp_path, monkeypatch):
    """Replacing server-side tenant resolution with a tool argument leaks a row."""
    state = tmp_path / "tenants.db"
    monkeypatch.setenv("MNEME_STATE", str(state))
    monkeypatch.setenv("MNEME_TENANT", "tenant-b")
    memory = AgentMemory(state)
    memory.store.add_memory("tenant-a-memory", "L1", "tenant A secret", [],
                            "test", "test", user="tenant-a")
    memory.store.add_memory("tenant-b-memory", "L1", "tenant B secret", [],
                            "test", "test", user="tenant-b")

    def call(name, arguments):
        return _rpc("tools/call", {"name": name, "arguments": arguments})["result"]

    absent = call("mneme.provenance", {"memory_id": "does-not-exist"})
    foreign_read = call("mneme.provenance", {"memory_id": "tenant-a-memory"})
    assert absent["isError"] is True
    assert foreign_read["isError"] is True
    assert foreign_read["content"] == absent["content"]

    spoofed_read = call("mneme.provenance", {
        "memory_id": "tenant-a-memory", "user": "tenant-a"})
    assert spoofed_read["isError"] is True
    assert "unknown argument" in spoofed_read["content"][0]["text"]

    foreign_forget = call("mneme.forget", {"memory_id": "tenant-a-memory"})
    assert foreign_forget["isError"] is True
    assert foreign_forget["content"] == absent["content"]
    assert AgentMemory(state).store.memory("tenant-a-memory") is not None

    spoofed_forget = call("mneme.forget", {
        "memory_id": "tenant-a-memory", "user": "tenant-a"})
    assert spoofed_forget["isError"] is True
    assert "unknown argument" in spoofed_forget["content"][0]["text"]
    assert AgentMemory(state).store.memory("tenant-a-memory") is not None

    owner_read = call("mneme.provenance", {"memory_id": "tenant-b-memory"})
    assert owner_read["isError"] is False
    assert json.loads(owner_read["content"][0]["text"])["memory_id"] == "tenant-b-memory"

    owner_forget = call("mneme.forget", {"memory_id": "tenant-b-memory"})
    assert owner_forget["isError"] is False
    assert AgentMemory(state).store.memory("tenant-b-memory") is None

    monkeypatch.delenv("MNEME_TENANT")
    missing_config = call("mneme.forget", {"memory_id": "tenant-a-memory"})
    assert missing_config["isError"] is True
    assert "tenant is not configured" in missing_config["content"][0]["text"]
    assert AgentMemory(state).store.memory("tenant-a-memory") is not None


def test_mcp_notification_without_id_gets_no_response():
    assert handle_request({"jsonrpc": "2.0", "method": "initialized"}) is None
