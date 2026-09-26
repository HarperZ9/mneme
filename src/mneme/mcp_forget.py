"""mcp_forget.py: the MCP forget tool, in two steps.

An MCP result enters a model context and so reaches that model's provider.
The tool therefore returns ids and counts, shows memory text only when the
caller asks for previews, and never returns the openings of the erase
commitments. A call with only `memory_id` returns the plan and deletes
nothing; a second call with `confirm_plan_sha256` applies exactly that plan.

The model that asked for the plan can send the digest too, so this step does
not tell the owner apart from a model. A host that launches mneme for an agent
should require an owner-granted operation for this tool.
"""
from __future__ import annotations

import json
import re

from .erase import EraseTargetNotFound, Selection, apply_erase, plan_erase

ALLOWED = {"memory_id", "reason", "keep_sources", "include_previews",
           "confirm_plan_sha256"}
TOOL = {
    "name": "mneme.forget",
    "description": "Erase a memory with its source turns and everything derived from "
                   "them (scenarios, persona lines, supersession history). Without "
                   "confirm_plan_sha256 it returns the plan (ids, counts, collateral "
                   "that shares a source turn) and deletes nothing; with the plan's "
                   "digest it applies exactly that plan and returns a receipt of what "
                   "it removed and what residue remains. Text previews only with "
                   "include_previews.",
    "inputSchema": {"type": "object", "required": ["memory_id"], "properties": {
        "memory_id": {"type": "string"},
        "reason": {"type": "string",
                   "description": "stored verbatim in the audit log; may not repeat "
                                  "erased text"},
        "keep_sources": {"type": "boolean",
                         "description": "keep the source turns (default false)"},
        "include_previews": {"type": "boolean",
                             "description": "show the text of collateral and lineage "
                                            "rows in the plan (default false)"},
        "confirm_plan_sha256": {"type": "string",
                                "description": "the plan_sha256 of the plan to apply"}}},
}


def _flag(args: dict, name: str) -> bool:
    value = args.get(name, False)
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean when provided")
    return value


def _checked(args: dict) -> tuple[str, str, str | None]:
    extra = set(args) - ALLOWED
    if extra:
        raise ValueError(f"unknown argument(s): {sorted(extra)}; allowed: {sorted(ALLOWED)}")
    memory_id, reason = args.get("memory_id"), args.get("reason", "")
    if not isinstance(memory_id, str) or not memory_id:
        raise ValueError("memory_id must be a non-empty string")
    if not isinstance(reason, str):
        raise ValueError("reason must be a string when provided")
    confirm = args.get("confirm_plan_sha256")
    if confirm is not None and (not isinstance(confirm, str)
                                or re.fullmatch(r"[0-9a-f]{64}", confirm) is None):
        raise ValueError("confirm_plan_sha256 must be 64 lowercase hex characters")
    return memory_id, reason, confirm


def call(memory, args: dict) -> str:
    memory_id, reason, confirm = _checked(args)
    selection = Selection(memories=(memory_id,), keep_sources=_flag(args, "keep_sources"))
    previews = _flag(args, "include_previews") and confirm is None
    try:
        plan = plan_erase(memory.store, selection, previews=previews)
    except EraseTargetNotFound as exc:
        raise ValueError(str(exc)) from exc
    if confirm is None:
        plan["next"] = ("nothing was deleted; to apply this exact plan, call "
                        "mneme.forget again with confirm_plan_sha256")
        return json.dumps(plan, indent=2, ensure_ascii=False)
    receipt = apply_erase(memory.store, selection, confirm, reason=reason)
    return json.dumps(receipt, indent=2, ensure_ascii=False)
