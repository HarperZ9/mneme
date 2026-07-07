"""Falsifiers for the white-box memory inspector.

It must faithfully show every layer with provenance + the live drift verdict +
the audit log, self-contained and offline.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory
from mneme.inspect import build_inspect, render_inspect_html

TURNS = [
    {"id": "t1", "role": "user", "text": "My name is Dana and I live in Denver."},
    {"id": "t2", "role": "user", "text": "I prefer dark roast coffee."},
]


def _mem():
    m = AgentMemory(":memory:")
    m.remember("s", TURNS)
    m.build_scenarios("s")
    m.persona("s")
    return m


def test_snapshot_covers_every_layer_with_provenance_and_verdict():
    snap = build_inspect(_mem())
    assert snap["counts"]["turns"] == 2
    assert snap["counts"]["L1"] == 2 and snap["counts"]["L2"] >= 1 and snap["counts"]["L3"] == 1
    for m in snap["layers"]["L1"]:
        assert m["source_ids"] and m["verdict"] in ("MATCH", "DRIFT", "UNVERIFIABLE")
    # fresh store: atoms are MATCH
    assert all(m["verdict"] == "MATCH" for m in snap["layers"]["L1"])


def test_inspector_shows_drift_after_a_source_changes():
    m = _mem()
    m.store.add_turn("t1", "s", "user", "My name is Dana and I live in Seattle now.")
    snap = build_inspect(m)
    verdicts = [x["verdict"] for x in snap["layers"]["L1"]]
    assert "DRIFT" in verdicts                    # the inspector surfaces the drift


def test_inspector_shows_the_audit_log():
    m = _mem()
    mid = m.store.memories(layer="L1")[0]["id"]
    m.forget(mid, reason="user requested")
    snap = build_inspect(m)
    assert snap["audit"]["entries"] == 1
    assert snap["audit"]["chain_intact"] is True
    assert snap["audit"]["log"][0]["op"] == "forget"


def test_page_is_self_contained():
    page = render_inspect_html(build_inspect(_mem()))
    assert page.lstrip().startswith("<!doctype html>")
    for marker in ("http://", "https://", "src=", "@import", "fetch("):
        assert marker not in page, f"external reference: {marker}"
    assert "mneme memory inspector" in page
    assert "atoms (L1)" in page and "audit log" in page


def test_deterministic():
    a = render_inspect_html(build_inspect(_mem()))
    b = render_inspect_html(build_inspect(_mem()))
    assert a == b


def test_cli_inspect_writes_html(tmp_path, capsys):
    from types import SimpleNamespace

    from mneme.cli import cmd_inspect

    db = tmp_path / "mem.db"
    m = AgentMemory(str(db))
    m.remember("s", TURNS)
    m.close()
    out = tmp_path / "inspector.html"
    assert cmd_inspect(SimpleNamespace(state=str(db), out=str(out))) == 0
    page = out.read_text(encoding="utf-8")
    assert "Denver" in page and "atoms (L1)" in page
    assert "memory inspector ->" in capsys.readouterr().err
