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
from mneme.compose import _canonical_json, _record_hash
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
        assert set(meas["recheck"]) == {
            "schema", "oracle", "memory_id", "grounding_sha256",
            "measurement_contract_sha256",
        }
        assert meas["recheck"]["schema"] == "mneme.recheck/1"
        assert meas["recheck"]["oracle"] == "mneme:drift/v1"
        assert meas["recheck"]["memory_id"] == meas["claim"]
        assert all(
            len(meas["recheck"][key]) == 64
            and set(meas["recheck"][key]) <= set("0123456789abcdef")
            for key in ("grounding_sha256", "measurement_contract_sha256")
        )
        assert not ({"path", "command", "argv", "cwd", "environment", "shell"}
                    & set(meas["recheck"]))


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


def test_descriptor_hash_canonical_json_golden_vector():
    grounding = {
        "memory_id": "m-\u03c0",
        "content_sha256": "a" * 64,
        "layer": "L1",
        "session": "s-\u03b1",
        "tenant": "u-\u03b2",
        "extractor": "rule/v1",
        "criterion": "atomic user fact",
        "source_ids": ["turn-2", "turn-1"],
        "source_hashes": {"turn-1": "b" * 64, "turn-2": "c" * 64},
    }
    measurement = {
        "claim": "m-\u03c0",
        "deviation": 0.0,
        "tolerance": 0.5,
        "method": "mneme.drift/v1",
        "evidence": ["mneme-memory:m-\u03c0"],
    }

    grounding_bytes = _canonical_json(grounding).encode("utf-8")
    measurement_bytes = _canonical_json(measurement).encode("utf-8")

    assert grounding_bytes.hex() == (
        "7b22636f6e74656e745f736861323536223a2261616161616161616161616161616161616161616161616161616161616161616161616161616161616161616161616161616161616161616161616161616161222c22637269746572696f6e223a2261746f6d696320757365722066616374222c22657874726163746f72223a2272756c652f7631222c226c61796572223a224c31222c226d656d6f72795f6964223a226d2dcf80222c2273657373696f6e223a22732dceb1222c22736f757263655f686173686573223a7b227475726e2d31223a2262626262626262626262626262626262626262626262626262626262626262626262626262626262626262626262626262626262626262626262626262626262222c227475726e2d32223a2263636363636363636363636363636363636363636363636363636363636363636363636363636363636363636363636363636363636363636363636363636363227d2c22736f757263655f696473223a5b227475726e2d32222c227475726e2d31225d2c2274656e616e74223a22752dceb2227d"
    )
    assert measurement_bytes.hex() == (
        "7b22636c61696d223a226d2dcf80222c22646576696174696f6e223a302e302c2265766964656e6365223a5b226d6e656d652d6d656d6f72793a6d2dcf80225d2c226d6574686f64223a226d6e656d652e64726966742f7631222c22746f6c6572616e6365223a302e357d"
    )
    assert _record_hash(grounding) == "147e42a850409699a7364dc2864d8feb6b3a4e105b7973bbc098c562aca352f6"
    assert _record_hash(measurement) == "85c640accf75da8f192c3bf65afdb2cf4ebda55ac6c13d6a746c829d21b42ae5"


def test_descriptor_creation_rejects_malformed_provenance_shape():
    memory = AgentMemory(":memory:")
    memory.store.add_turn("a", "s", "user", "source a")
    memory.store.add_turn("b", "s", "user", "source b")
    memory.store.add_memory("m-ab", "L1", "derived", ["a", "b"],
                            "fixture/v1", "fixture", session="s")
    memory.store.conn.execute(
        "UPDATE memories SET source_ids=? WHERE id=?", ('"ab"', "m-ab"))
    memory.store.conn.commit()

    with pytest.raises(ValueError, match="provenance"):
        memory.to_crucible("s")


def test_cli_and_note_guide_the_composition():
    export = _mem().to_crucible("s")
    assert export["schema"] == "mneme.crucible-export/2"
    assert "recompute" in export["note"].lower()
    assert "does not independently re-read the source" in export["note"].lower()
    assert "crucible assess" in export["recheck"]
