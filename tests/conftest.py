"""Shared test setup.

Replay snapshots live in a per-user state directory. Every test gets its own
snapshot root under pytest's temporary tree, so no test reads or writes the
real per-user directory, and a test that asks for `snapshot_root` can look at
exactly what Mneme left there.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def snapshot_root(tmp_path_factory, monkeypatch) -> Path:
    import mneme.snapshot_dir as snapshot_dir

    root = tmp_path_factory.mktemp("state") / "mneme" / "snapshots"
    monkeypatch.setattr(snapshot_dir, "platform_snapshot_root", lambda: root)
    return root
