"""mcp.py — mneme over MCP stdio, so an agent can use accountable memory directly.

The class ships agent-facing search tools (tdai_memory_search); mneme ships the
same surface plus the receipts. Tools: remember, recall, drift, provenance,
forget, audit. Every recall returns its re-derivable RecallReceipt as the tool
result, so an agent (or its operator) can see and re-check why a memory was
surfaced. Zero-dep JSON-RPC 2.0 over stdio, matching the MCP protocol the
sibling flagships speak.

The DB path comes from the MNEME_STATE env var (default mneme.db), so a host
config points one server at one memory store.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from . import __version__
from .memory import AgentMemory

MCP_PROTOCOL_VERSION = "2025-06-18"


def _state_path() -> str:
    return os.environ.get("MNEME_STATE", "mneme.db")


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
                           "description": "list of {role, text} turns"}}}},
        {"name": "mneme.recall",
         "description": "Retrieve memories for a query and return a re-derivable "
                        "ranking receipt (hits with bm25/vector/fused scores).",
         "inputSchema": {"type": "object", "required": ["query"],
             "properties": {
                 "query": {"type": "string"},
                 "strategy": {"type": "string", "enum": ["keyword", "vector", "hybrid"]},
                 "top_k": {"type": "integer"}}}},
        {"name": "mneme.drift",
         "description": "Verdict every memory's grounding against the current "
                        "store (MATCH / DRIFT / UNVERIFIABLE).",
         "inputSchema": {"type": "object", "properties": {
             "layer": {"type": "string", "description": "L1 (default), L2, L3"}}}},
        {"name": "mneme.provenance",
         "description": "Show a memory's provenance receipt (sources, extractor, hash).",
         "inputSchema": {"type": "object", "required": ["memory_id"],
             "properties": {"memory_id": {"type": "string"}}}},
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
    ]


def call_tool(name: str, args: dict) -> str:
    mem = AgentMemory(_state_path())
    if name == "mneme.remember":
        summary = mem.remember(str(args["session"]), list(args["turns"]))
        return json.dumps(summary, indent=2, ensure_ascii=False)
    if name == "mneme.recall":
        receipt = mem.recall(str(args["query"]),
                             strategy=str(args.get("strategy", "hybrid")),
                             top_k=int(args.get("top_k", 5)))
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
            request = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps(_err(None, -32700, "parse error")) + "\n")
            stdout.flush()
            continue
        response = handle_request(request)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0
