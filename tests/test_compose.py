"""Falsifiers for the mneme -> crucible schema interoperability boundary.

Load-bearing: (1) every memory exports as a Crucible claim with a real
falsification; (2) Mneme drift verdicts map to loadable measurements, including
fail-closed UNVERIFIABLE; (3) Crucible's own loader and assessor independently
recompute deterministic MATCH/DRIFT/UNVERIFIABLE verdicts from those
Mneme-supplied measurements. This does not independently verify the source;
source re-reading remains Mneme-provided unless an external recheck oracle is
attached.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from mneme import AgentMemory
from mneme.drift import DRIFT, MATCH, UNVERIFIABLE, MemoryVerdict

TURNS = [
    {"id": "t1", "role": "user", "text": "My name is Dana and I live in Denver."},
    {"id": "t2", "role": "user", "text": "I prefer dark roast coffee."},
]


def _mem():
    m = AgentMemory(":memory:")
    m.remember("s", TURNS)
    return m


def _import_crucible_or_skip():
    """Import Crucible without making standalone mneme depend on its checkout."""
    try:
        import crucible  # noqa: F401 - verifies an installed package first
        return
    except ModuleNotFoundError as exc:
        if exc.name != "crucible":
            raise

    repo_root = Path(__file__).resolve().parents[1]
    workspace_root = repo_root.parents[1]
    candidates = []
    configured = os.environ.get("MNEME_CRUCIBLE_SRC")
    if configured:
        configured_path = Path(configured).expanduser()
        candidates.extend((configured_path, configured_path / "src"))
    candidates.extend((
        repo_root.parent / "crucible" / "src",
        workspace_root / "public" / "crucible" / "src",
        workspace_root / "crucible" / "src",
    ))

    for candidate in dict.fromkeys(path.resolve() for path in candidates):
        if not candidate.is_dir():
            continue
        sys.path.insert(0, str(candidate))
        try:
            import crucible  # noqa: F401 - package presence is the optional boundary
            return
        except ModuleNotFoundError as exc:
            if exc.name != "crucible":
                raise
            sys.path.pop(0)

    pytest.skip("Crucible is neither installed nor available from a sibling source checkout")


def test_every_memory_exports_as_a_testable_claim():
    export = _mem().to_crucible("s")
    assert export["thesis"]["claims"]
    for c in export["thesis"]["claims"]:
        assert c["text"] and c["falsification"], "claims must carry a falsification"
    # a fresh store: every drift-derived measurement says the claim holds
    claim_ids = {claim["id"] for claim in export["thesis"]["claims"]}
    for meas in export["measurements"]:
        assert meas["claim"] in claim_ids
        assert meas["deviation"] == 0.0
        assert meas["tolerance"] == 0.5
        assert meas["method"] == "mneme.drift/v1"
        assert meas["mneme_verdict"] == "MATCH"


def test_drift_flows_into_the_measurement():
    m = _mem()
    denver = next(r for r in m.store.memories(layer="L1") if "denver" in r["text"].lower())
    # change the source under the atom -> its measurement should flip to not-holding
    m.store.add_turn("t1", "s", "user", "My name is Dana and I live in Seattle.")
    export = m.to_crucible("s")
    drifted = next(meas for meas in export["measurements"] if meas["claim"] == denver["id"])
    assert drifted["mneme_verdict"] == DRIFT
    assert drifted["deviation"] == 1.0


def test_unverifiable_memory_exports_no_deviation(monkeypatch):
    import mneme.compose

    def unmeasurable(_store, memory_id):
        return MemoryVerdict(memory_id, UNVERIFIABLE, "source missing", (), ("t1",))

    monkeypatch.setattr(mneme.compose, "check_memory", unmeasurable)
    export = _mem().to_crucible("s")

    assert export["measurements"]
    for meas in export["measurements"]:
        assert meas["mneme_verdict"] == UNVERIFIABLE
        assert meas["deviation"] is None


def test_export_round_trips_through_crucibles_own_pipeline(tmp_path):
    _import_crucible_or_skip()
    from crucible.assess import assess
    from crucible.commands import _load_measurements, _thesis_from_data
    from crucible.thesis import verify_thesis

    memory = AgentMemory(":memory:")
    memory.remember("s", TURNS + [
        {"id": "t3", "role": "user", "text": "My emergency contact is Morgan Lee."},
    ])
    export = memory.to_crucible("s")
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(export), encoding="utf-8")
    thesis = _thesis_from_data(export["thesis"], clock=lambda: 1000.0)
    assert verify_thesis(thesis)
    measurements = _load_measurements(thesis, str(export_path))
    assert len(measurements) == len(export["measurements"])
    record, verdicts = assess(thesis, measurements, clock=lambda: 1000.0)
    assert record.claims == len(export["thesis"]["claims"])
    assert record.match == record.claims
    assert [verdict.status for verdict in verdicts] == [MATCH] * record.claims

    state_rows = []
    expected_states = ((MATCH, 0.0), (DRIFT, 1.0), (UNVERIFIABLE, None))
    for claim, (status, deviation) in zip(thesis.claims, expected_states, strict=True):
        state_rows.append({
            "claim": claim.id,
            "deviation": deviation,
            "tolerance": 0.5,
            "method": "mneme.drift/v1",
            "evidence": [f"mneme-memory:{claim.id}"],
            "mneme_verdict": status,
        })
    state_export = {**export, "measurements": state_rows}
    export_path.write_text(json.dumps(state_export), encoding="utf-8")
    state_measurements = _load_measurements(thesis, str(export_path))
    state_record, state_verdicts = assess(thesis, state_measurements, clock=lambda: 1000.0)

    assert [(measurement.claim_id, measurement.deviation) for measurement in state_measurements] == [
        (claim.id, deviation) for claim, (_, deviation) in zip(thesis.claims, expected_states, strict=True)
    ]
    assert [verdict.status for verdict in state_verdicts] == [MATCH, DRIFT, UNVERIFIABLE]
    assert (state_record.match, state_record.drift, state_record.unverifiable) == (1, 1, 1)


def test_export_is_deterministic():
    a = _mem().to_crucible("s")
    b = _mem().to_crucible("s")
    assert a == b


def test_cli_and_note_guide_the_composition():
    export = _mem().to_crucible("s")
    assert export["schema"] == "mneme.crucible-export/2"
    assert "recompute" in export["note"].lower()
    assert "does not independently re-read the source" in export["note"].lower()
    assert "crucible assess" in export["recheck"]
