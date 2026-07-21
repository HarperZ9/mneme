"""Compose Mneme drift measurements into a Crucible-compatible export.

Mneme supplies source-bound drift measurements. Crucible can independently
recompute a verdict from those measurements, but does not independently re-read
the source unless an external recheck oracle is attached. The export remains
zero-dependency: Mneme emits JSON-compatible dictionaries and never imports
Crucible.
"""
from __future__ import annotations

from .drift import DRIFT, MATCH, UNVERIFIABLE, check_memory


def _deviation(verdict: str) -> float | None:
    """Translate Mneme's drift state to Crucible's measurable deviation."""
    if verdict == MATCH:
        return 0.0
    if verdict == DRIFT:
        return 1.0
    return None


def to_crucible_thesis(memory, session: str | None = None,
                       layer: str = "L1") -> dict:
    """Export memories and Mneme drift measurements for Crucible assessment."""
    rows = memory.store.memories(layer=layer, session=session)
    claims = []
    measurements = []
    for r in rows:
        text = r["text"].rstrip(".")
        claim_text = f"The memory holds: {text}."
        falsification = ("a Mneme drift check returns DRIFT; UNVERIFIABLE "
                         "leaves the claim undetermined")
        claims.append({"id": r["id"], "text": claim_text, "falsification": falsification})
        verdict = check_memory(memory.store, r["id"]).verdict
        measurements.append({
            "claim": r["id"],
            "deviation": _deviation(verdict),
            "tolerance": 0.5,
            "method": "mneme.drift/v1",
            "evidence": [f"mneme-memory:{r['id']}"],
            "mneme_verdict": verdict,
        })
    thesis = {
        "title": f"mneme memory faithfulness — {session or 'all sessions'}",
        "disposition": "publishable",
        "claims": claims,
        "source": "mneme-export",
    }
    return {
        "schema": "mneme.crucible-export/2",
        "thesis": thesis,
        "measurements": measurements,
        "note": ("Crucible recomputes its verdict from Mneme's drift measurement; "
                 "it does not independently re-read the source unless an external "
                 "recheck oracle is attached."),
        "recheck": "crucible register thesis.json && crucible assess thesis.json --measurements m.json",
    }
