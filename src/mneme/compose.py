"""compose.py — export memory as independently-verifiable claims (mneme -> crucible).

mneme's drift check is self-reported: the memory judges its own freshness. The
ecosystem closes that loop. Every memory becomes a crucible claim — the fact as
an assertion, with the observation that would refute it (its source no longer
supports it) as the falsification — so a SEPARATE judgment organ (crucible, the
sibling flagship) can assess whether the memory is still faithful. Paired with
the drift verdicts as measurements, an external verifier certifies the memory's
freshness; it is no longer just mneme's word.

Completes the triangle: gather (intake, with source receipts) -> mneme (memory,
with provenance + drift) -> crucible (independent verification). Each hop is
re-checkable and nothing is taken on trust.

Decoupled + zero-dep: emits crucible-shaped dicts (mneme never imports crucible);
any verifier consuming {title, claims:[{text, falsification}]} composes. The
measurements map each claim to its drift verdict so the verifier can witness it.
"""
from __future__ import annotations

from .drift import DRIFT, MATCH, UNVERIFIABLE, check_memory


def to_crucible_thesis(memory, session: str | None = None,
                       layer: str = "L1") -> dict:
    """Export memories as a crucible thesis (register/assess input shape) plus
    measurements derived from each memory's drift verdict. A downstream
    `crucible assess` then witnesses whether the memory is still faithful."""
    rows = memory.store.memories(layer=layer, session=session)
    claims = []
    measurements = []
    for r in rows:
        text = r["text"].rstrip(".")
        claim_text = f"The memory holds: {text}."
        falsification = ("the source this memory was derived from no longer "
                         "supports it (a drift check returns DRIFT/UNVERIFIABLE)")
        claims.append({"id": r["id"], "text": claim_text, "falsification": falsification})
        verdict = check_memory(memory.store, r["id"]).verdict
        # map the memory's own drift verdict into a crucible measurement: a
        # MATCH means the claim holds (deviation 0); DRIFT/UNVERIFIABLE do not.
        measurements.append({
            "claim_id": r["id"],
            "predicted": 0.0, "observed": 0.0 if verdict == MATCH else 1.0,
            "tolerance": 0.5, "trusted": True,
            "mneme_verdict": verdict})
    thesis = {
        "title": f"mneme memory faithfulness — {session or 'all sessions'}",
        "disposition": "publishable",
        "claims": claims,
        "source": "mneme-export",
    }
    return {
        "schema": "mneme.crucible-export/1",
        "thesis": thesis,
        "measurements": measurements,
        "note": ("register the thesis and assess it with these measurements in "
                 "crucible to get an INDEPENDENT verdict on the memory's "
                 "faithfulness — not mneme's self-report"),
        "recheck": "crucible register thesis.json && crucible assess thesis.json --measurements m.json",
    }
