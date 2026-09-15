"""Falsifiers for L2 scenarios and the MCP server — the feature-parity layer.

L2: atoms cluster deterministically into scene blocks, each citing its atoms so
it stays drift-checkable. MCP: the agent-facing tools work over stdio JSON-RPC
and a recall through MCP carries the same re-derivable receipt.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory
from mneme.mcp import handle_request, serve

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
                     "mneme.provenance", "mneme.origin_recheck",
                     "mneme.forget", "mneme.audit", "mneme.status",
                     "mneme.doctor", "mneme.to_crucible",
                     "mneme.replay_crucible"}




def _mcp_call_text(name, arguments=None):
    resp = _rpc("tools/call", {"name": name, "arguments": arguments or {}})
    assert "error" not in resp
    assert resp["result"].get("isError") is not True
    return json.loads(resp["result"]["content"][0]["text"])


def _measurement_seal(rows):
    fields = (
        "claim_id", "claim_sha256", "deviation", "tolerance", "method",
        "measured_at", "evidence", "recheck",
    )
    objects = [{key: row.get(key) for key in fields} for row in rows]
    objects.sort(key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False))
    canonical = json.dumps(objects, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay_binding(rows, *, skipped_count=0):
    objects = [
        {"recheck": row["recheck"], "expected_measurement": row["expected_measurement"]}
        for row in rows
    ]
    objects.sort(key=lambda value: json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    canonical = json.dumps(objects, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "schema": "crucible.replay-set/1",
        "descriptor_count": len(objects),
        "skipped_count": skipped_count,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _template_from_export(export):
    rows = []
    for index, measurement in enumerate(export["measurements"], 1):
        claim_id = measurement["claim"]
        claim_sha = hashlib.sha256(f"claim:{claim_id}".encode("utf-8")).hexdigest()
        expected = {
            "claim_id": claim_id,
            "claim_sha256": claim_sha,
            "deviation": measurement["deviation"],
            "tolerance": measurement["tolerance"],
            "method": measurement["method"],
            "measured_at": 1_700_000_000.0 + index,
            "evidence": measurement["evidence"],
        }
        rows.append({
            "claim": {"id": claim_id, "sha256": claim_sha, "text": f"claim {index}", "status": "MATCH"},
            "recheck": measurement["recheck"],
            "expected_measurement": expected,
            "measurement": {**expected, "deviation": None, "measured_at": None, "evidence": []},
        })
    sealed_rows = [{**row["expected_measurement"], "recheck": row["recheck"]} for row in rows]
    return {
        "schema": "crucible.replay-template/1",
        "assessment": {
            "thesis_id": "mcp-fixture",
            "assessment_seal": hashlib.sha256(b"assessment").hexdigest(),
            "measurement_seal": _measurement_seal(sealed_rows),
        },
        "replay_binding": _replay_binding(rows),
        "instructions": "test fixture",
        "replays": rows,
    }


def test_mcp_to_crucible_exports_schema_v2_without_runtime_authority_in_descriptors(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEME_STATE", str(tmp_path / "mcp-export.db"))
    _mcp_call_text("mneme.remember", {"session": "s", "turns": TURNS, "user": "operator"})

    export = _mcp_call_text("mneme.to_crucible", {
        "session": "s", "layer": "L1", "user": "operator"})

    assert export["schema"] == "mneme.crucible-export/2"
    assert len(export["thesis"]["claims"]) == 3
    assert len(export["measurements"]) == 3
    descriptors = [row["recheck"] for row in export["measurements"]]
    assert {d["schema"] for d in descriptors} == {"mneme.recheck/1"}
    assert {d["oracle"] for d in descriptors} == {"mneme:drift/v1"}
    assert all("command" not in d and "state" not in d and "db" not in d for d in descriptors)


def test_mcp_to_crucible_requires_explicit_user_or_all_users(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEME_STATE", str(tmp_path / "mcp-user-scope.db"))
    _mcp_call_text("mneme.remember", {"session": "shared", "user": "alice", "turns": [
        {"id": "t1", "role": "user", "text": "My launch codename is Orchid."},
    ]})
    _mcp_call_text("mneme.remember", {"session": "shared", "user": "bob", "turns": [
        {"id": "t1", "role": "user", "text": "My launch codename is Granite."},
    ]})

    unscoped = _rpc("tools/call", {
        "name": "mneme.to_crucible",
        "arguments": {"session": "shared"},
    })
    assert "error" not in unscoped
    assert unscoped["result"]["isError"] is True
    assert "requires explicit user or all_users=true" in unscoped["result"]["content"][0]["text"]

    alice = _mcp_call_text("mneme.to_crucible", {"session": "shared", "user": "alice"})
    alice_claims = [claim["text"] for claim in alice["thesis"]["claims"]]
    assert any("Orchid" in claim for claim in alice_claims)
    assert all("Granite" not in claim for claim in alice_claims)

    all_users = _mcp_call_text("mneme.to_crucible", {
        "session": "shared", "all_users": True})
    all_claims = [claim["text"] for claim in all_users["thesis"]["claims"]]
    assert any("Orchid" in claim for claim in all_claims)
    assert any("Granite" in claim for claim in all_claims)


@pytest.mark.parametrize("env_value", [None, ""])
def test_mcp_crucible_operations_require_explicit_state_binding(
        tmp_path, monkeypatch, env_value):
    if env_value is None:
        monkeypatch.delenv("MNEME_STATE", raising=False)
    else:
        monkeypatch.setenv("MNEME_STATE", env_value)
    monkeypatch.chdir(tmp_path)
    constructed = []

    class RecordingMemory:
        def __init__(self, *args, **kwargs):
            constructed.append({"args": args, "kwargs": kwargs})

        def to_crucible(self, **kwargs):
            return {"schema": "mneme.crucible-export/2"}

        def replay_crucible(self, template):
            return {"schema": "crucible.replay-pack/1"}

        def close(self):
            return None

    monkeypatch.setattr("mneme.mcp.AgentMemory", RecordingMemory)

    for name, arguments in (
        ("mneme.to_crucible", {"all_users": True}),
        ("mneme.replay_crucible", {
            "template": {"schema": "crucible.replay-template/1"},
        }),
    ):
        resp = _rpc("tools/call", {"name": name, "arguments": arguments})

        assert "error" not in resp
        assert resp["result"]["isError"] is True
        assert "MNEME_STATE is required" in resp["result"]["content"][0]["text"]

    assert constructed == []


@pytest.mark.parametrize(("arguments", "message"), [
    ({"user": "operator", "session": None}, "session must be a non-empty string"),
    ({"user": "operator", "session": 42}, "session must be a non-empty string"),
    ({"user": "operator", "session": {"id": "s"}}, "session must be a non-empty string"),
    ({"user": "operator", "session": ""}, "session must be a non-empty string"),
    ({"user": "operator", "layer": 42}, "layer must be one of L1, L2, L3"),
    ({"user": "operator", "layer": ""}, "layer must be one of L1, L2, L3"),
    ({"user": "operator", "layer": "L4"}, "layer must be one of L1, L2, L3"),
])
def test_mcp_to_crucible_rejects_malformed_export_selectors(
        tmp_path, monkeypatch, arguments, message):
    monkeypatch.setenv("MNEME_STATE", str(tmp_path / "mcp-selector.db"))
    _mcp_call_text("mneme.remember", {
        "session": "s",
        "turns": TURNS,
        "user": "operator",
    })

    resp = _rpc("tools/call", {
        "name": "mneme.to_crucible",
        "arguments": arguments,
    })

    assert "error" not in resp
    assert resp["result"]["isError"] is True
    assert message in resp["result"]["content"][0]["text"]


def test_mcp_replay_crucible_returns_pack_from_bound_state_not_descriptor_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEME_STATE", str(tmp_path / "mcp-replay.db"))
    _mcp_call_text("mneme.remember", {"session": "s", "turns": TURNS, "user": "operator"})
    export = _mcp_call_text("mneme.to_crucible", {"session": "s", "user": "operator"})
    template = _template_from_export(export)

    pack = _mcp_call_text("mneme.replay_crucible", {"template": template})

    assert pack["schema"] == "crucible.replay-pack/1"
    assert pack["assessment"] == template["assessment"]
    assert pack["replay_binding"] == template["replay_binding"]
    assert len(pack["replays"]) == 3
    assert {row["mneme_verdict"] for row in pack["replays"]} == {"MATCH"}
    assert all(row["measurement"]["deviation"] == 0.0 for row in pack["replays"])
    assert "measurement_seal_rows" not in pack
    assert all("command" not in row["recheck"] and "state" not in row["recheck"]
               and "db" not in row["recheck"] for row in pack["replays"])


def test_mcp_replay_rejects_omitted_row_with_recomputed_positive_skip_binding(
        tmp_path, monkeypatch):
    monkeypatch.setenv("MNEME_STATE", str(tmp_path / "mcp-replay-omit.db"))
    _mcp_call_text("mneme.remember", {"session": "s", "turns": TURNS, "user": "operator"})
    export = _mcp_call_text("mneme.to_crucible", {"session": "s", "user": "operator"})
    template = _template_from_export(export)
    template["replays"].pop()
    template["replay_binding"] = _replay_binding(
        template["replays"], skipped_count=1)

    resp = _rpc("tools/call", {"name": "mneme.replay_crucible",
                               "arguments": {"template": template}})

    assert "error" not in resp
    assert resp["result"]["isError"] is True
    assert "skipped rows require a verifier-enforced full denominator" in resp["result"]["content"][0]["text"]




def test_mcp_to_crucible_does_not_create_missing_state(tmp_path, monkeypatch):
    missing = tmp_path / "missing-export.db"
    monkeypatch.setenv("MNEME_STATE", str(missing))

    resp = _rpc("tools/call", {"name": "mneme.to_crucible",
                               "arguments": {"all_users": True}})

    assert "error" not in resp
    assert resp["result"]["isError"] is True
    assert not missing.exists()


def test_mcp_replay_crucible_does_not_create_missing_state(tmp_path, monkeypatch):
    missing = tmp_path / "missing-replay.db"
    monkeypatch.setenv("MNEME_STATE", str(missing))

    resp = _rpc("tools/call", {"name": "mneme.replay_crucible",
                               "arguments": {"template": {"schema": "crucible.replay-template/1"}}})

    assert "error" not in resp
    assert resp["result"]["isError"] is True
    assert not missing.exists()


def test_mcp_replay_crucible_rejects_non_template_as_tool_error(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEME_STATE", str(tmp_path / "mcp-replay-error.db"))
    _mcp_call_text("mneme.remember", {"session": "s", "turns": TURNS, "user": "operator"})
    resp = _rpc("tools/call", {"name": "mneme.replay_crucible",
                               "arguments": {"template": {"schema": "wrong"}}})

    assert "error" not in resp
    assert resp["result"]["isError"] is True
    assert "unsupported replay template schema" in resp["result"]["content"][0]["text"]


def test_mcp_replay_crucible_reports_private_snapshot_cleanup_warning(
        tmp_path, monkeypatch):
    monkeypatch.setenv("MNEME_STATE", str(tmp_path / "mcp-replay-cleanup.db"))
    _mcp_call_text("mneme.remember", {"session": "s", "turns": TURNS, "user": "operator"})
    export = _mcp_call_text("mneme.to_crucible", {"session": "s", "user": "operator"})
    template = _template_from_export(export)
    original_close = AgentMemory.close

    def close_with_warning(self):
        warning = original_close(self)
        if self.store.immutable_snapshot:
            return warning or "private replay cleanup incomplete: simulated"
        return warning

    monkeypatch.setattr(AgentMemory, "close", close_with_warning)

    resp = _rpc("tools/call", {"name": "mneme.replay_crucible",
                               "arguments": {"template": template}})

    assert "error" not in resp
    assert resp["result"]["isError"] is True
    assert "private replay cleanup incomplete: simulated" in resp["result"]["content"][0]["text"]


def test_mcp_replay_crucible_reports_cleanup_warning_when_replay_rejects(
        tmp_path, monkeypatch):
    monkeypatch.setenv("MNEME_STATE", str(tmp_path / "mcp-replay-reject-cleanup.db"))
    _mcp_call_text("mneme.remember", {"session": "s", "turns": TURNS, "user": "operator"})
    export = _mcp_call_text("mneme.to_crucible", {"session": "s", "user": "operator"})
    template = _template_from_export(export)
    template["replays"].pop()
    template["replay_binding"] = _replay_binding(
        template["replays"], skipped_count=1)
    original_close = AgentMemory.close

    def close_with_warning(self):
        warning = original_close(self)
        if self.store.immutable_snapshot:
            return warning or "private replay cleanup incomplete: simulated"
        return warning

    monkeypatch.setattr(AgentMemory, "close", close_with_warning)

    resp = _rpc("tools/call", {"name": "mneme.replay_crucible",
                               "arguments": {"template": template}})
    text = resp["result"]["content"][0]["text"]

    assert "error" not in resp
    assert resp["result"]["isError"] is True
    assert "skipped rows require a verifier-enforced full denominator" in text
    assert "private replay cleanup incomplete: simulated" in text
    assert str(tmp_path) not in text


def test_mcp_serve_rejects_duplicate_keys_inside_replay_template(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEME_STATE", str(tmp_path / "mcp-duplicate-template.db"))
    _mcp_call_text("mneme.remember", {"session": "s", "turns": TURNS, "user": "operator"})
    export = _mcp_call_text("mneme.to_crucible", {"session": "s", "user": "operator"})
    template_text = json.dumps(_template_from_export(export))
    template_text = template_text.replace(
        '"schema": "crucible.replay-template/1"',
        '"schema": "wrong", "schema": "crucible.replay-template/1"',
        1,
    )
    request = (
        '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":'
        '{"name":"mneme.replay_crucible","arguments":{"template":'
        + template_text + '}}}\n'
    )
    output = io.StringIO()

    serve(io.StringIO(request), output)

    resp = json.loads(output.getvalue())
    assert resp["error"]["code"] == -32700
    assert "duplicate JSON key 'schema'" in resp["error"]["message"]


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
    r = _rpc("tools/call", {"name": "mneme.provenance",
                            "arguments": {"memory_id": "does-not-exist"}})
    assert r["result"]["isError"] is True               # not a JSON-RPC error frame
    assert "no memory" in r["result"]["content"][0]["text"]


def test_mcp_notification_without_id_gets_no_response():
    assert handle_request({"jsonrpc": "2.0", "method": "initialized"}) is None


def test_status_and_doctor_health_tools():
    # the Flywheel lane probe marks mneme LIVE only if a status/doctor tool answers
    for name in ("mneme.status", "mneme.doctor"):
        r = _rpc("tools/call", {"name": name})
        body = json.loads(r["result"]["content"][0]["text"])
        assert body["ok"] is True and body["server"] == "mneme"
        assert r["result"].get("isError") is not True
