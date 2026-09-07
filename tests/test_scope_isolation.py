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
from mneme.drift import MATCH, check_memory
from mneme.extract import extract_atoms
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


def test_cross_tenant_same_content_is_partitioned_not_rejected():
    m = AgentMemory(":memory:")
    m.remember("chat1", [{"role": "user", "text": "I live in Denver."}], user="alice")
    alice = m.store.memories(layer="L1", user="alice")
    assert alice
    aid = alice[0]["id"]

    m.remember("chat1", [{"role": "user", "text": "I live in Denver."}], user="bob")

    assert m.store.memory(aid)["user"] == "alice"
    assert len(m.store.memories(layer="L1", user="alice")) == 1
    assert len(m.store.memories(layer="L1", user="bob")) == 1
    assert m.store.memories(layer="L1", user="alice")[0]["id"] != (
        m.store.memories(layer="L1", user="bob")[0]["id"]
    )


def test_supplied_turn_id_is_partitioned_by_user_and_session():
    m = AgentMemory(":memory:")
    first = m.remember("session-a", [{
        "id": "turn-1",
        "role": "user",
        "text": "I work on project Alpha.",
    }], user="user-a")
    first_memory_id = first["provenance"][0]["memory_id"]
    first_source_id = first["provenance"][0]["source_ids"][0]
    assert check_memory(m.store, first_memory_id).verdict == MATCH

    second = m.remember("session-b", [{
        "id": "turn-1",
        "role": "user",
        "text": "I work on project Beta.",
    }], user="user-b")

    second_source_id = second["provenance"][0]["source_ids"][0]
    assert first_source_id != second_source_id
    assert m.store.turn(first_source_id)["session"] == "session-a"
    assert m.store.turn(second_source_id)["session"] == "session-b"
    assert "Alpha" in m.store.turn(first_source_id)["text"]
    assert "Beta" in m.store.turn(second_source_id)["text"]
    assert check_memory(m.store, first_memory_id).verdict == MATCH
    scoped = m.recall("project", user="user-a", session="session-a")
    assert scoped.hits and all("Beta" not in hit.text for hit in scoped.hits)


def test_same_user_can_reuse_supplied_turn_id_in_different_sessions():
    m = AgentMemory(":memory:")
    first = m.remember("session-a", [{
        "id": "turn-1",
        "role": "user",
        "text": "I prefer Alpha notes.",
    }], user="user-a")
    second = m.remember("session-b", [{
        "id": "turn-1",
        "role": "user",
        "text": "I prefer Beta notes.",
    }], user="user-a")

    first_source_id = first["provenance"][0]["source_ids"][0]
    second_source_id = second["provenance"][0]["source_ids"][0]
    assert first_source_id != second_source_id
    assert check_memory(m.store, first["provenance"][0]["memory_id"]).verdict == MATCH
    assert check_memory(m.store, second["provenance"][0]["memory_id"]).verdict == MATCH


def test_default_user_preserves_legacy_source_id_and_idempotence():
    m = AgentMemory(":memory:")
    first = m.remember("s", [{
        "id": "turn-1",
        "role": "user",
        "text": "I live in Denver.",
    }])
    second = m.remember("s", [{
        "id": "turn-1",
        "role": "user",
        "text": "I live in Denver.",
    }])

    assert first["provenance"][0]["source_ids"] == ["turn-1"]
    assert second["provenance"][0]["source_ids"] == ["turn-1"]
    assert len(m.store.turns()) == 1
    assert len(m.store.memories(layer="L1")) == 1


def test_default_user_rejects_reserved_internal_source_id_without_partial_writes():
    m = AgentMemory(":memory:")

    with pytest.raises(ValueError, match="reserved internal source id"):
        m.remember("s", [{
            "id": "src:v1:aaaaaaaaaaaaaaaa",
            "role": "user",
            "text": "I live in Denver.",
        }])

    assert m.store.turns() == []
    assert m.store.memories(layer="L1") == []


def test_named_user_legacy_raw_source_reimport_reuses_record_without_new_ordinals():
    m = AgentMemory(":memory:")
    text = "I live in Denver."
    legacy_source_id = "turn-1"
    m.store.add_turn(legacy_source_id, "s", "user", text)
    [(memory_id, atom)] = extract_atoms(
        [{"id": legacy_source_id, "role": "user", "text": text}],
        m.extractor,
    )
    m.store.add_memory(
        memory_id,
        "L1",
        atom.text,
        [atom.source_id],
        m.extractor.name,
        "atomic user fact",
        session="s",
        user="alice",
    )
    legacy_turn_ord = m.store.turn(legacy_source_id)["ord"]
    legacy_memory_ord = m.store.memory(memory_id)["created_ord"]

    summary = m.remember("s", [{
        "id": legacy_source_id,
        "role": "user",
        "text": text,
    }], user="alice")

    assert summary["provenance"][0]["source_ids"] == [legacy_source_id]
    assert len(m.store.memories(layer="L1", user="alice")) == 1
    assert m.store.turn(legacy_source_id)["ord"] == legacy_turn_ord
    assert m.store.memory(memory_id)["created_ord"] == legacy_memory_ord
    assert check_memory(m.store, memory_id).verdict == MATCH


def test_default_user_legacy_citation_makes_named_reimport_use_partitioned_source():
    m = AgentMemory(":memory:")
    text = "I live in Denver."
    legacy_source_id = "turn-1"
    m.store.add_turn(legacy_source_id, "s", "user", text)
    m.store.add_memory(
        "default-note",
        "L1",
        "default-user note from turn-1",
        [legacy_source_id],
        "fixture/v1",
        "fixture criterion",
        session="s",
        user="",
    )
    [(memory_id, atom)] = extract_atoms(
        [{"id": legacy_source_id, "role": "user", "text": text}],
        m.extractor,
    )
    m.store.add_memory(
        memory_id,
        "L1",
        atom.text,
        [atom.source_id],
        m.extractor.name,
        "atomic user fact",
        session="s",
        user="alice",
    )

    summary = m.remember("s", [{
        "id": legacy_source_id,
        "role": "user",
        "text": text,
    }], user="alice")

    source_id = summary["provenance"][0]["source_ids"][0]
    assert source_id != legacy_source_id
    assert source_id.startswith("src:v1:")
    assert m.store.turn(legacy_source_id)["text"] == text
    assert m.store.memory("default-note")["user"] == ""


def test_repeated_current_import_does_not_advance_source_or_memory_ordinals():
    m = AgentMemory(":memory:")
    first = m.remember("s", [{
        "id": "turn-1",
        "role": "user",
        "text": "I live in Denver.",
    }], user="alice")
    source_id = first["provenance"][0]["source_ids"][0]
    memory_id = first["provenance"][0]["memory_id"]
    turn_ord = m.store.turn(source_id)["ord"]
    memory_ord = m.store.memory(memory_id)["created_ord"]

    second = m.remember("s", [{
        "id": "turn-1",
        "role": "user",
        "text": "I live in Denver.",
    }], user="alice")

    assert second["provenance"][0]["memory_id"] == memory_id
    assert second["provenance"][0]["source_ids"] == [source_id]
    assert m.store.turn(source_id)["ord"] == turn_ord
    assert m.store.memory(memory_id)["created_ord"] == memory_ord
    assert check_memory(m.store, memory_id).verdict == MATCH


def test_memory_id_collision_is_rejected_before_source_turn_write():
    m = AgentMemory(":memory:")
    text = "I live in Denver."
    [(memory_id, atom)] = extract_atoms(
        [{"id": "turn-1", "role": "user", "text": text}],
        m.extractor,
    )
    m.store.add_memory(
        memory_id,
        "L1",
        atom.text,
        [atom.source_id],
        m.extractor.name,
        "atomic user fact",
        session="s",
        user="bob",
    )

    with pytest.raises(ValueError, match="already owned by user"):
        m.remember("s", [{
            "id": "turn-1",
            "role": "user",
            "text": text,
        }])

    assert m.store.turns() == []
    assert m.store.memory(memory_id)["user"] == "bob"


def test_conflicting_duplicate_turn_id_batch_is_rejected_without_partial_writes():
    m = AgentMemory(":memory:")

    with pytest.raises(ValueError, match="conflicting turn id"):
        m.remember("session-a", [
            {"id": "turn-1", "role": "user", "text": "I prefer Alpha notes."},
            {"id": "turn-1", "role": "user", "text": "I prefer Beta notes."},
        ], user="user-a")

    assert m.store.turns() == []
    assert m.store.memories(layer="L1") == []


def test_invalid_turn_batch_is_rejected_without_partial_writes():
    m = AgentMemory(":memory:")

    with pytest.raises(ValueError, match="turn 1 missing required field 'text'"):
        m.remember("session-a", [
            {"id": "turn-1", "role": "user", "text": "I prefer Alpha notes."},
            {"id": "turn-2", "role": "user"},
        ], user="user-a")

    assert m.store.turns() == []
    assert m.store.memories(layer="L1") == []
