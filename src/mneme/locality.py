"""locality.py: say whether this memory can send text off the machine.

The store is a local file, and mneme's own code opens no network connection.
Two edges can: an extractor and an embedder that a library caller injects
(`AgentMemory(extractor=..., embedder=...)`). Text given to either goes to
that model's provider. `describe` reports which ones this instance uses, so a
program can show its user the answer instead of asking them to read the code.
The CLI and the MCP server always run the built-in, local ones.
"""
from __future__ import annotations

from .embed import NgramEmbedder
from .extract import RuleExtractor

LOCAL = "built-in, runs on this machine"
INJECTED = ("supplied by the caller; text given to it can go to that model's "
            "provider, and mneme cannot see where it sends it")


def _edge(value, builtin: type) -> dict:
    if value is None:
        return {"name": None, "local": True, "note": "off"}
    local = type(value) is builtin
    return {"name": getattr(value, "name", type(value).__name__), "local": local,
            "note": LOCAL if local else INJECTED}


def describe(memory) -> dict:
    """Which extractor and embedder this AgentMemory uses, and whether each is local."""
    extractor = _edge(memory.extractor, RuleExtractor)
    embedder = _edge(memory.embedder, NgramEmbedder)
    return {"schema": "mneme.locality/1", "extractor": extractor, "embedder": embedder,
            "sends_text_off_machine": not (extractor["local"] and embedder["local"])}
