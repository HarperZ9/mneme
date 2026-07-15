"""Falsifiers for tenant scope isolation — no cross-boundary bleed.

Load-bearing credo repairs:
  1. the MCP surface can scope recall to a user and threads a remember `user`
     (it does not silently union all tenants or drop the partition);
  2. an unknown MCP argument is rejected, not silently dropped;
  3. persona/build_scenarios read and write inside one user's partition;
  4. consolidate never merges (deletes) one tenant's memory for another's;
  5. a cross-tenant id collision is rejected, not a silent overwrite.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from mneme import AgentMemory
from mneme.mcp import handle_request


def _rpc(method, params=None, mid=1):
    return handle_request({"jsonrpc": "2.0", "id": mid, "method": method,
                           "params": params or {}})


def test_mcp_recall_scopes_by_user(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEME_STATE", str(tmp_path / "s.db"))
    _rpc("tools/call", {"name": "mneme.remember", "arguments": {
        "session": "chat1", "turns": [{"role": "user", "text": "I live in Denver."}],
        "user": "alice"}})
    _rpc("tools/call", {"name": "mneme.remember", "arguments": {
        "session": "chat1", "turns": [{"role": "user", "text": "I live in Boston."}],
        "user": "bob"}})
    rec = _rpc("tools/call", {"name": "mneme.recall", "arguments": {
        "query": "where does the user live", "strategy": "keyword", "user": "alice"}})
    receipt = json.loads(rec["result"]["content"][0]["text"])
    assert receipt["hits"]
    assert all("boston" not in h["text"].lower() for h in receipt["hits"])   # never bob's
    assert receipt["scope"]["user"] == "alice"                               # scope witnessed


def test_mcp_remember_rejects_unknown_argument(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEME_STATE", str(tmp_path / "u.db"))
    r = _rpc("tools/call", {"name": "mneme.remember", "arguments": {
        "session": "s", "turns": [{"role": "user", "text": "I live in Denver."}],
        "usr": "alice"}})   # typo'd key must not be silently dropped
    assert r["result"]["isError"] is True
    assert "unknown argument" in r["result"]["content"][0]["text"]


def test_persona_is_scoped_to_one_user():
    m = AgentMemory(":memory:")
    m.remember("chat1", [{"role": "user", "text": "I live in Denver."}], user="alice")
    m.remember("chat1", [{"role": "user", "text": "I live in Boston."}], user="bob")
    out = m.persona("chat1", user="alice")
    assert "Denver" in out["text"] and "Boston" not in out["text"]
    l3_alice = m.store.memories(layer="L3", user="alice")
    assert l3_alice and "Boston" not in l3_alice[0]["text"]
    assert m.store.memories(layer="L3", user="bob") == []   # not filed under the shared ""


def test_build_scenarios_is_scoped_to_one_user():
    m = AgentMemory(":memory:")
    m.remember("chat1", [{"role": "user", "text": "I love hiking the Rockies."},
                         {"role": "user", "text": "I hike the Rockies most weekends."}],
               user="alice")
    m.remember("chat1", [{"role": "user", "text": "I collect vintage cameras."}], user="bob")
    m.build_scenarios("chat1", user="alice")
    l2 = m.store.memories(layer="L2", user="alice")
    assert l2 and all("camera" not in x["text"].lower() for x in l2)
    assert m.store.memories(layer="L2", user="bob") == []


def test_consolidate_never_merges_across_users():
    m = AgentMemory(":memory:")
    m.remember("chat1", [{"role": "user", "text": "I live in Denver with my dog."}], user="alice")
    m.remember("chat2", [{"role": "user", "text": "I live in Denver."}], user="bob")
    report = m.consolidate()   # defaults: all sessions, all users, apply
    assert report["merged_away"] == 0
    assert m.store.memories(layer="L1", user="alice"), "alice's atom must survive"
    assert m.store.memories(layer="L1", user="bob"), "bob's atom must survive"


def test_cross_tenant_id_collision_is_rejected_not_silently_overwritten():
    m = AgentMemory(":memory:")
    m.remember("chat1", [{"role": "user", "text": "I live in Denver."}], user="alice")
    alice = m.store.memories(layer="L1", user="alice")
    assert alice
    aid = alice[0]["id"]
    # bob stores the identical sentence in the same session -> same content-derived id
    with pytest.raises(ValueError):
        m.remember("chat1", [{"role": "user", "text": "I live in Denver."}], user="bob")
    # alice still owns her memory; it was not reassigned to bob
    assert m.store.memory(aid)["user"] == "alice"
