"""The README's diagrams are generated from a spec, so they can go stale the way any
other derived file goes stale: somebody edits a stage name, nobody re-renders, and the
picture describes a version of mneme that no longer exists. The gate re-renders from the
spec and compares bytes. This runs the gate under pytest and asserts on its receipt, so
a drifted drawing fails the suite instead of quietly shipping.

Below that, the grounding-verdicts card gets a second layer. The gates settle
whether it fits its columns; whether it is TRUE of drift.py is settled by
driving every condition it draws against a real store."""

import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_GATE = _REPO / "tools" / "check_repo_art.py"

GATES = (
    "spec.present",
    "art.matches_spec",
    "art.render_is_deterministic",
    "art.identity_per_repository",
    "art.seed_is_recorded",
    "art.no_local_paths_or_em_dashes",
    "art.spec_words_reach_the_drawing",
    "art.note_survives_the_wrapper",
    "art.return_edge_stays_on_its_row",
    "art.every_illustration_is_shown",
    "art.tagline_stays_inside_its_rule",
    "art.outcome_fits_its_box",
    "art.card_draws_shapes_not_digits",
    "art.card_text_fits_its_column",
    "art.card_carries_one_mark",
    "art.card_alt_reaches_the_readme",
    "art.the_gate_can_fail",
)

DRAWINGS = (
    "docs/art/mneme-header.svg",
    "docs/art/recall-lane.svg",
    "docs/art/drift-lane.svg",
    "docs/art/grounding-verdicts.svg",
)


def _receipt() -> dict:
    out = subprocess.run([sys.executable, str(_GATE), "--json"],
                         cwd=_REPO, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    return json.loads(out.stdout)


def test_every_gate_passes_and_the_receipt_names_what_it_ran():
    receipt = _receipt()
    assert receipt["schema"] == "mneme.repo-art/v1"
    assert [c["name"] for c in receipt["checks"]] == list(GATES)
    assert all(c["passed"] for c in receipt["checks"]), \
        [c for c in receipt["checks"] if not c["passed"]]


def test_both_diagrams_and_the_mark_are_accounted_for():
    receipt = _receipt()
    assert receipt["specs"] == ["docs/art/mneme.art.json"]
    drawn = {out["file"]: out for out in receipt["outputs"]}
    assert set(drawn) == set(DRAWINGS)
    for path, out in drawn.items():
        assert len(out["sha256"]) == 64, path
        assert out["bytes"] > 0, path


def test_a_gate_that_cannot_fail_is_not_a_gate(tmp_path, monkeypatch):
    """Point the outcome-box check at a note too wide for its box and it has to
    complain. Without this, a green suite proves only that the gate ran."""
    sys.path.insert(0, str(_REPO / "tools"))
    import check_repo_art as gate
    spec = json.loads((_REPO / "docs" / "art" / "mneme.art.json").read_text("utf-8"))
    spec["flows"][0]["outcomes"][0]["note"] = "x" * 80
    (tmp_path / "mneme.art.json").write_text(json.dumps(spec), encoding="utf-8")
    monkeypatch.setattr(gate, "ART", tmp_path)
    assert len(gate.check_outcome_fits_its_box([])) == 1


# docs/art/grounding-verdicts.svg draws nine conditions a grounding check can
# land on and the verdict each one produces. That is a claim about drift.py,
# not about the picture, so nothing in tools/ can settle it. Each row below is
# driven against a real store and the returned verdict is held against the one
# the row draws.
sys.path.insert(0, str(_REPO / "src"))

from mneme import AgentMemory  # noqa: E402
from mneme.drift import check_memory  # noqa: E402


def _grounded():
    """One turn, and one memory extracted from it. Everything agrees."""
    memory = AgentMemory(":memory:")
    memory.store.add_turn("t1", "s", "user", "I live in Denver.")
    memory.store.add_memory("m1", "L1", "lives in Denver", ["t1"],
                            "fixture/v1", "residence", session="s")
    return memory


def _sql(memory, statement, *args):
    """Write around the store, the way a hand edit to the database would."""
    memory.store.conn.execute(statement, args)
    memory.store.conn.commit()


def _missing_memory(memory):
    return "nope"


def _unreadable_provenance(memory):
    _sql(memory, "UPDATE memories SET source_hashes=? WHERE id=?",
         "not json", "m1")
    return "m1"


def _no_sources(memory):
    memory.store.add_memory("m0", "L1", "no grounding", [], "fixture/v1",
                            "none", session="s")
    return "m0"


def _altered_row(memory):
    _sql(memory, "UPDATE memories SET text=? WHERE id=?",
         "lives in Berlin", "m1")
    return "m1"


def _source_deleted(memory):
    _sql(memory, "DELETE FROM turns WHERE id=?", "t1")
    return "m1"


def _source_tampered(memory):
    # Text changed and content_sha256 left alone, which is what a writer going
    # around the store leaves behind.
    _sql(memory, "UPDATE turns SET text=? WHERE id=?", "I live in Berlin.", "t1")
    return "m1"


def _snapshot_dropped(memory):
    _sql(memory, "UPDATE memories SET source_hashes=? WHERE id=?", "{}", "m1")
    return "m1"


def _source_replaced(memory):
    # Through the store, so the source's own address is recomputed and agrees
    # with its bytes. Only the extraction snapshot disagrees.
    memory.store.add_turn("t1", "s", "user", "I live in Berlin.")
    return "m1"


def _unchanged(memory):
    return "m1"


# Keyed by the row the card draws, so a row renamed in the spec and not here
# fails as an unreached condition rather than passing quietly.
CONDITIONS = {
    "memory_missing": _missing_memory,
    "provenance_unreadable": _unreadable_provenance,
    "no_sources_cited": _no_sources,
    "memory_row_altered": _altered_row,
    "source_row_gone": _source_deleted,
    "source_bytes_changed": _source_tampered,
    "snapshot_never_taken": _snapshot_dropped,
    "source_moved_since": _source_replaced,
    "all_sources_unchanged": _unchanged,
}


def _card():
    spec = json.loads(
        (_REPO / "docs" / "art" / "mneme.art.json").read_text("utf-8"))
    return next(c for c in spec["cards"]
                if c["file"] == "grounding-verdicts.svg")


def test_every_condition_the_card_draws_has_something_that_reaches_it():
    drawn = [f["key"] for f in _card()["fields"]]
    assert sorted(drawn) == sorted(CONDITIONS), \
        "a drawn row has no driver, or a driver draws no row"


def test_each_row_returns_the_verdict_it_draws():
    for field in _card()["fields"]:
        memory = _grounded()
        memory_id = CONDITIONS[field["key"]](memory)
        assert check_memory(memory.store, memory_id).verdict == field["value"], \
            field["key"]


def test_the_marked_row_is_the_one_a_stored_hash_alone_would_miss():
    """The accent claims a source is re-hashed from its fields before the
    address stored beside it is believed. Drive the tamper the accent names,
    then check that trusting the stored address alone would have said MATCH."""
    assert [f["key"] for f in _card()["fields"]
            if f.get("tone", "none") != "none"] == ["source_bytes_changed"]

    memory = _grounded()
    before = memory.store.turn("t1")["content_sha256"]
    _source_tampered(memory)
    after = memory.store.turn("t1")
    # The stale address survived the edit, so a check that read it would agree.
    assert after["content_sha256"] == before
    assert after["text"] == "I live in Berlin."
    assert check_memory(memory.store, "m1").verdict == "DRIFT"
