"""The example tour must stay runnable (it doubles as an integration smoke)."""
import subprocess
import sys
from pathlib import Path


def test_tour_runs_clean():
    tour = Path(__file__).resolve().parents[1] / "examples" / "tour.py"
    r = subprocess.run([sys.executable, str(tour)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "traces to the source it was gathered from" in r.stdout
