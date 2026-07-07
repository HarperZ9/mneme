"""Falsifiers for the mneme -> crucible composition: memory faithfulness made
independently verifiable.

Load-bearing: (1) every memory exports as a crucible claim with a REAL
falsification (never an untestable claim); (2) the drift verdict maps into a
measurement so a fresh memory verifies and a drifted one does not; (3) the
export round-trips through crucible's OWN register/assess when crucible is
importable — proving the memory is verifiable by a separate organ, not just
shaped like it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from mneme import AgentMemory

TURNS = [
    {"id": "t1", "role": "user", "text": "My name is Dana and I live in Denver."},
    {"id": "t2", "role": "user", "text": "I prefer dark roast coffee."},
]


def _mem():
    m = AgentMemory(":memory:")
    m.remember("s", TURNS)
    return m


def test_every_memory_exports_as_a_testable_claim():
    export = _mem().to_crucible("s")
    assert export["thesis"]["claims"]
    for c in export["thesis"]["claims"]:
        assert c["text"] and c["falsification"], "claims must carry a falsification"
    # a fresh store: every drift-derived measurement says the claim holds
    for meas in export["measurements"]:
        assert meas["mneme_verdict"] == "MATCH"
        assert meas["observed"] == 0.0


def test_drift_flows_into_the_measurement():
    m = _mem()
    denver = next(r for r in m.store.memories(layer="L1") if "denver" in r["text"].lower())
    # change the source under the atom -> its measurement should flip to not-holding
    m.store.add_turn("t1", "s", "user", "My name is Dana and I live in Seattle.")
    export = m.to_crucible("s")
    drifted = next(meas for meas in export["measurements"] if meas["claim_id"] == denver["id"])
    assert drifted["mneme_verdict"] in ("DRIFT", "UNVERIFIABLE")
    assert drifted["observed"] == 1.0            # the claim no longer holds


def test_export_round_trips_through_crucibles_own_pipeline():
    try:
        sys.path.insert(0, str(Path("C:/dev/public/crucible/src")))
        from crucible.assess import assess
        from crucible.commands import _thesis_from_data
        from crucible.measure import Measurement
        from crucible.thesis import verify_thesis
    except Exception:
        pytest.skip("crucible not importable here")

    export = _mem().to_crucible("s")
    # the thesis loads via crucible's OWN loader and verifies
    thesis = _thesis_from_data(export["thesis"], clock=lambda: 1000.0)
    assert verify_thesis(thesis)
    # and assess witnesses it (no measurements -> all UNVERIFIABLE, but it runs)
    record, verdicts = assess(thesis)
    assert record.claims == len(export["thesis"]["claims"])


def test_export_is_deterministic():
    a = _mem().to_crucible("s")
    b = _mem().to_crucible("s")
    assert a == b


def test_cli_and_note_guide_the_composition():
    export = _mem().to_crucible("s")
    assert export["schema"] == "mneme.crucible-export/1"
    assert "independent" in export["note"].lower()
    assert "crucible assess" in export["recheck"]
