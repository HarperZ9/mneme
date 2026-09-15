"""mcp.py — mneme over MCP stdio, so an agent can use accountable memory directly.

The server exposes tools for remember, recall, drift, provenance, local origin
recheck, Crucible export/replay, forget, audit, status, and doctor. Recall returns its re-derivable
RecallReceipt as the tool result, so an agent or operator can see and re-check
why a memory was surfaced. The implementation is zero-dependency JSON-RPC 2.0
over stdio.

The DB path comes from the MNEME_STATE env var (default mneme.db for legacy
tools). Crucible export/replay require an explicit MNEME_STATE binding so a host
cannot accidentally read an unrelated mneme.db from its working directory.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from . import __version__
from .memory import AgentMemory

MCP_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_LAYERS = {"L1", "L2", "L3"}


class DuplicateJsonKeyError(ValueError):
    """A JSON-RPC request is ambiguous because an object repeats a key."""


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJsonKeyError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _state_path() -> str:
    return os.environ.get("MNEME_STATE", "mneme.db")


def _required_state_path(tool_name: str) -> str:
    state_path = os.environ.get("MNEME_STATE")
    if state_path is None or not state_path.strip():
        raise ValueError(f"MNEME_STATE is required for {tool_name}")
    return state_path


def _ok(mid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _text(text: str, *, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _tool_defs() -> list[dict]:
    return [
        {"name": "mneme.remember",
         "description": "Record conversation turns (L0) and extract atomic facts "
                        "(L1) with provenance. Idempotent.",
         "inputSchema": {"type": "object", "required": ["session", "turns"],
             "properties": {
                 "session": {"type": "string"},
                 "turns": {"type": "array", "items": {"type": "object"},
                           "description": "list of {role, text} turns"},
                 "user": {"type": "string",
                          "description": "tenant to scope this memory to (default shared \"\")"}}}},
        {"name": "mneme.recall",
         "description": "Retrieve memories for a query and return a re-derivable "
                        "ranking receipt (hits with bm25/vector/fused scores).",
         "inputSchema": {"type": "object", "required": ["query"],
             "properties": {
                 "query": {"type": "string"},
                 "strategy": {"type": "string", "enum": ["keyword", "vector", "hybrid"]},
                 "top_k": {"type": "integer"},
                 "user": {"type": "string",
                          "description": "scope recall to one tenant (omit = across all)"},
                 "session": {"type": "string",
                             "description": "scope recall to one session (omit = across all)"}}}},
        {"name": "mneme.drift",
         "description": "Verdict every memory's grounding against the current "
                        "store (MATCH / DRIFT / UNVERIFIABLE).",
         "inputSchema": {"type": "object", "properties": {
             "layer": {"type": "string", "description": "L1 (default), L2, L3"}}}},
        {"name": "mneme.to_crucible",
         "description": "Export memories as mneme.crucible-export/2: claims, "
                        "source-bound drift measurements, and declarative "
                        "mneme.recheck/1 descriptors for Crucible.",
         "inputSchema": {"type": "object", "properties": {
             "session": {"type": "string",
                         "description": "optional session to export"},
             "user": {"type": "string",
                      "description": "explicit user selector; empty string selects the shared default user"},
             "all_users": {"type": "boolean",
                           "description": "deliberately export all users in the configured state"},
             "layer": {"type": "string",
                       "description": "L1 (default), L2, L3"}}}},
        {"name": "mneme.replay_crucible",
         "description": "Consume a crucible.replay-template/1 object and return "
                        "a crucible.replay-pack/1 from the server-bound Mneme state.",
         "inputSchema": {"type": "object", "required": ["template"],
             "properties": {
                 "template": {"type": "object",
                              "description": "decoded crucible.replay-template/1 object"}}}},
        {"name": "mneme.provenance",
          "description": "Show a memory's provenance receipt (sources, extractor, hash).",
          "inputSchema": {"type": "object", "required": ["memory_id"],
              "properties": {"memory_id": {"type": "string"}}}},
        {"name": "mneme.origin_recheck",
         "description": "Opt-in local origin freshness check for supported "
                        "Gather docs/file-read receipts under an allowed root.",
         "inputSchema": {"type": "object", "required": ["memory_id", "allowed_root"],
             "properties": {
                 "memory_id": {"type": "string"},
                 "allowed_root": {"type": "string"},
                 "profile": {"type": "string",
                             "description": "default gather.docs.file-read/v1"}}}},
        {"name": "mneme.forget",
         "description": "Delete a memory, leaving an auditable tombstone (what "
                        "was forgotten, its hash, why).",
         "inputSchema": {"type": "object", "required": ["memory_id"],
             "properties": {"memory_id": {"type": "string"},
                            "reason": {"type": "string"}}}},
        {"name": "mneme.audit",
         "description": "The hash-chained history of every forget/update, with a "
                        "chain-intact verdict.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "mneme.status",
         "description": "Liveness and identity of the mneme MCP server (name, version, protocol). Network-free health probe.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "mneme.doctor",
         "description": "Readiness diagnostic: identity plus the configured state-db path and the tools exposed.",
         "inputSchema": {"type": "object", "properties": {}}},
    ]


def _reject_unknown(args: dict, allowed: set[str]) -> None:
    """A dropped argument is a silent scope error (e.g. a `user` an agent passed
    that never reached the store). Fail loudly instead of misfiling the write."""
    extra = set(args) - allowed
    if extra:
        raise ValueError(f"unknown argument(s): {sorted(extra)}; allowed: {sorted(allowed)}")


def _optional_bool(args: dict, name: str) -> bool | None:
    value = args.get(name)
    if value is None:
        return None
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean when provided")
    return value


def _export_user_selector(args: dict) -> str | None:
    has_user = "user" in args
    has_all_users = "all_users" in args
    if has_user and has_all_users:
        raise ValueError("user and all_users are exclusive")
    all_users = _optional_bool(args, "all_users")
    if all_users is True:
        return None
    if not has_user:
        raise ValueError("mneme.to_crucible requires explicit user or all_users=true")
    user = args["user"]
    if not isinstance(user, str):
        raise ValueError("user must be a string when provided")
    return user


def _optional_session_selector(args: dict) -> str | None:
    if "session" not in args:
        return None
    session = args["session"]
    if not isinstance(session, str) or session == "":
        raise ValueError("session must be a non-empty string when provided")
    return session


def _export_layer_selector(args: dict) -> str:
    layer = args.get("layer", "L1")
    if not isinstance(layer, str) or layer not in SUPPORTED_LAYERS:
        raise ValueError("layer must be one of L1, L2, L3")
    return layer


def call_tool(name: str, args: dict) -> str:
    if name in ("mneme.status", "mneme.doctor"):
        info = {"ok": True, "server": "mneme", "version": __version__,
                "protocol": MCP_PROTOCOL_VERSION}
        if name == "mneme.doctor":
            info["state_path"] = _state_path()
            info["tools"] = [t["name"] for t in _tool_defs()]
        return json.dumps(info, indent=2, ensure_ascii=False)
    if name == "mneme.origin_recheck":
        _reject_unknown(args, {"memory_id", "allowed_root", "profile"})
        mem = AgentMemory(_state_path(), read_only=True)
        try:
            report = mem.recheck_local_origin(
                str(args["memory_id"]),
                allowed_root=str(args["allowed_root"]),
                profile=(str(args["profile"]) if "profile" in args else None),
            )
        finally:
            mem.close()
        return json.dumps(report, indent=2, ensure_ascii=False)
    if name == "mneme.to_crucible":
        _reject_unknown(args, {"session", "layer", "user", "all_users"})
        state_path = _required_state_path(name)
        user = _export_user_selector(args)
        session = _optional_session_selector(args)
        layer = _export_layer_selector(args)
        reader = AgentMemory(state_path, read_only=True)
        try:
            export = reader.to_crucible(
                session=session,
                layer=layer,
                user=user,
            )
        finally:
            reader.close()
        return json.dumps(export, indent=2, ensure_ascii=False)
    if name == "mneme.replay_crucible":
        _reject_unknown(args, {"template"})
        template = args.get("template")
        if not isinstance(template, dict):
            raise ValueError("template must be a crucible.replay-template/1 object")
        state_path = _required_state_path(name)
        replay_memory = AgentMemory(
            state_path,
            read_only=True,
            immutable_snapshot=True,
        )
        primary_error = None
        pack = None
        try:
            try:
                pack = replay_memory.replay_crucible(template)
            except Exception as exc:
                primary_error = exc
        finally:
            cleanup_warning = replay_memory.close()
        if cleanup_warning:
            if primary_error is not None:
                raise RuntimeError(f"{primary_error}; {cleanup_warning}") from primary_error
            raise RuntimeError(cleanup_warning)
        if primary_error is not None:
            raise primary_error
        return json.dumps(pack, indent=2, ensure_ascii=False)
    mem = AgentMemory(_state_path())
    if name == "mneme.remember":
        _reject_unknown(args, {"session", "turns", "user"})
        summary = mem.remember(str(args["session"]), list(args["turns"]),
                               user=str(args.get("user", "")))
        return json.dumps(summary, indent=2, ensure_ascii=False)
    if name == "mneme.recall":
        _reject_unknown(args, {"query", "strategy", "top_k", "user", "session"})
        receipt = mem.recall(str(args["query"]),
                             strategy=str(args.get("strategy", "hybrid")),
                             top_k=int(args.get("top_k", 5)),
                             user=(str(args["user"]) if "user" in args else None),
                             session=(str(args["session"]) if "session" in args else None))
        return json.dumps(receipt.as_dict(), indent=2, ensure_ascii=False)
    if name == "mneme.drift":
        return json.dumps(mem.drift(layer=args.get("layer", "L1")), indent=2, ensure_ascii=False)
    if name == "mneme.provenance":
        prov = mem.provenance(str(args["memory_id"]))
        if prov is None:
            raise ValueError(f"no memory with id {args['memory_id']!r}")
        return json.dumps(prov, indent=2, ensure_ascii=False)
    if name == "mneme.forget":
        entry = mem.forget(str(args["memory_id"]), reason=str(args.get("reason", "")))
        if entry is None:
            raise ValueError(f"no memory with id {args['memory_id']!r}")
        return json.dumps(entry, indent=2, ensure_ascii=False)
    if name == "mneme.audit":
        return json.dumps(mem.audit(), indent=2, ensure_ascii=False)
    raise ValueError(f"unknown tool: {name}")


def handle_request(req: dict) -> dict | None:
    method = req.get("method")
    mid = req.get("id")
    if "id" not in req:
        return None
    if method == "initialize":
        return _ok(mid, {"protocolVersion": MCP_PROTOCOL_VERSION,
                         "capabilities": {"tools": {}},
                         "serverInfo": {"name": "mneme", "version": __version__}})
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": _tool_defs()})
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        if not isinstance(name, str) or name not in {t["name"] for t in _tool_defs()}:
            return _err(mid, -32602, f"unknown tool: {name!r}")
        try:
            return _ok(mid, _text(call_tool(name, params.get("arguments") or {})))
        except Exception as exc:                    # tool errors ride the result, not the transport
            return _ok(mid, _text(f"error: {exc}", is_error=True))
    return _err(mid, -32601, f"method not found: {method}")


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line, object_pairs_hook=_unique_json_object)
        except DuplicateJsonKeyError as exc:
            stdout.write(json.dumps(_err(None, -32700, str(exc))) + "\n")
            stdout.flush()
            continue
        except json.JSONDecodeError:
            stdout.write(json.dumps(_err(None, -32700, "parse error")) + "\n")
            stdout.flush()
            continue
        response = handle_request(request)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0
