"""Compose Mneme drift measurements into a Crucible-compatible export.

Mneme supplies source-bound drift measurements. Crucible can independently
recompute a verdict from those measurements, but does not independently re-read
the source unless an external recheck oracle is attached. The export remains
zero-dependency: Mneme emits JSON-compatible dictionaries and never imports
Crucible.
"""
from __future__ import annotations

import json
from collections.abc import Mapping

from .drift import DRIFT, MATCH, UNVERIFIABLE, check_memory
from .receipt import content_hash, decode_provenance


def _deviation(verdict: str) -> float | None:
    """Translate Mneme's drift state to Crucible's measurable deviation."""
    if verdict == MATCH:
        return 0.0
    if verdict == DRIFT:
        return 1.0
    return None


def _canonical_json(value: Mapping[str, object]) -> str:
    """Canonical carrier used by Mneme replay descriptor hashes."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def _record_hash(value: Mapping[str, object]) -> str:
    """Bind one canonical JSON record through Mneme's existing hash primitive."""
    return content_hash(_canonical_json(value))


def _grounding_record(row: Mapping[str, object]) -> dict:
    """The non-portable grounding snapshot whose hash enters a descriptor."""
    source_ids, source_hashes = decode_provenance(
        row["source_ids"], row["source_hashes"])
    return {
        "memory_id": row["id"],
        "content_sha256": row["content_sha256"],
        "layer": row["layer"],
        "session": row["session"],
        "tenant": row["user"],
        "extractor": row["extractor"],
        "criterion": row["criterion"],
        # Source order is provenance and therefore intentionally not sorted.
        "source_ids": source_ids,
        "source_hashes": source_hashes,
    }


def _measurement_contract(measurement: Mapping[str, object]) -> dict:
    """Portable Mneme fields before Crucible adds claim hash and timestamp."""
    return {
        "claim": measurement["claim"],
        "deviation": measurement.get("deviation"),
        "tolerance": measurement["tolerance"],
        "method": measurement["method"],
        "evidence": list(measurement["evidence"]),
    }


def _recheck_descriptor(row: Mapping[str, object], measurement: Mapping[str, object]) -> dict:
    return {
        "schema": "mneme.recheck/1",
        "oracle": "mneme:drift/v1",
        "memory_id": row["id"],
        "grounding_sha256": _record_hash(_grounding_record(row)),
        "measurement_contract_sha256": _record_hash(_measurement_contract(measurement)),
    }


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
        measurement = {
            "claim": r["id"],
            "deviation": _deviation(verdict),
            "tolerance": 0.5,
            "method": "mneme.drift/v1",
            "evidence": [f"mneme-memory:{r['id']}"],
            "mneme_verdict": verdict,
        }
        measurement["recheck"] = _recheck_descriptor(r, measurement)
        measurements.append(measurement)
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
