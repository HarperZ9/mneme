"""mneme — accountable agent memory.

The 4-tier layered memory + hybrid retrieval the class expects, plus the three
things none of them ship: a provenance receipt on every memory, a recall receipt
that reproduces the ranking, and a drift verdict that makes a stale memory say
so. Zero runtime dependencies (stdlib sqlite3); the deterministic floor needs no
model and no API.
"""
from .memory import AgentMemory
from .receipt import ProvenanceReceipt, RecallReceipt

__version__ = "0.1.0"
__all__ = ["AgentMemory", "ProvenanceReceipt", "RecallReceipt"]
