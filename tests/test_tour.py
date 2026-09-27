"""The example tour must stay runnable (it doubles as an integration smoke).

It runs in this process, not as a subprocess, so the shared test setup
(conftest.py) applies: the tour's writable open and its erase sweep and scan
a snapshot root and a temp directory under pytest's tree, never the owner's
real LocalAppData or %TEMP%. An environment variable cannot redirect the
Windows Known Folder lookup, so a subprocess could not be isolated that way.
"""
import runpy
from pathlib import Path

TOUR = Path(__file__).resolve().parents[1] / "examples" / "tour.py"


def test_tour_runs_clean(capsys, snapshot_root):
    namespace = runpy.run_path(str(TOUR), run_name="mneme_tour")

    assert namespace["main"]() == 0
    assert "traces to the source it was gathered from" in capsys.readouterr().out
    assert not snapshot_root.exists() or not any(snapshot_root.rglob("mneme-replay-*"))
