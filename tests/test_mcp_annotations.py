"""Every MCP tool states its title and read/write hints.

The Anthropic Software Directory Policy requires readOnlyHint, destructiveHint
and title on every tool a listed server exposes.
"""
from mneme.mcp import TOOL_ANNOTATIONS, _tool_defs

HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def test_every_tool_is_annotated():
    tools = _tool_defs()
    assert {tool["name"] for tool in tools} == set(TOOL_ANNOTATIONS)
    for tool in tools:
        notes = tool["annotations"]
        assert notes["title"] and tool["title"] == notes["title"], tool["name"]
        assert all(isinstance(notes[key], bool) for key in HINTS), tool["name"]
        assert len(tool["name"]) <= 64
        if notes["readOnlyHint"]:
            assert notes["destructiveHint"] is False, tool["name"]


def test_hints_match_tool_effects():
    by_name = {tool["name"]: tool["annotations"] for tool in _tool_defs()}
    for name in ("mneme.status", "mneme.doctor", "mneme.to_crucible", "mneme.origin_recheck"):
        assert by_name[name]["readOnlyHint"] is True, name
    assert by_name["mneme.remember"]["readOnlyHint"] is False
    assert by_name["mneme.forget"]["destructiveHint"] is True
