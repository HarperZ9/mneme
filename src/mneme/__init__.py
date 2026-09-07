"""mneme — accountable agent memory.

Mneme provides the 4-tier layered memory model and hybrid retrieval that agent
workflows expect, while recording source provenance, returning recall receipts
that reproduce ranking, and reporting drift verdicts when checks run. Zero
runtime dependencies (stdlib sqlite3); the deterministic floor needs no model
and no API.
"""
from .memory import AgentMemory
from .recall import recall, verify_recall
from .receipt import ProvenanceReceipt, RecallReceipt

__version__ = "0.3.0"
__all__ = ["AgentMemory", "ProvenanceReceipt", "RecallReceipt", "recall", "verify_recall"]
