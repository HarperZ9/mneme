"""Falsifiers for the per-user snapshot root on each platform.

Every other test replaces the root with a temporary one, so these tests call
the real function (imported before the autouse fixture patches the module).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import mneme.snapshot_dir as snapshot_dir
from mneme.snapshot_dir import platform_snapshot_root

TAIL = ("mneme", "snapshots")


@pytest.mark.skipif(os.name != "nt", reason="Windows Known Folder API")
def test_windows_uses_the_local_appdata_known_folder():
    root = platform_snapshot_root()

    assert root == snapshot_dir._known_local_appdata() / Path(*TAIL)
    assert root.parts[-2:] == TAIL and root.is_absolute()


def test_windows_falls_back_to_the_home_directory_when_the_api_fails(monkeypatch):
    def fail():
        raise OSError("planted: SHGetKnownFolderPath failed")

    monkeypatch.setattr(snapshot_dir, "_known_local_appdata", fail)

    assert platform_snapshot_root(system="nt") == Path.home() / "AppData" / "Local" / Path(*TAIL)


def test_posix_uses_an_absolute_xdg_state_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert platform_snapshot_root(system="posix") == tmp_path / Path(*TAIL)


def test_posix_ignores_a_relative_or_empty_xdg_state_home(monkeypatch):
    expected = Path.home() / ".local" / "state" / Path(*TAIL)
    for value in ("relative/state", ""):
        monkeypatch.setenv("XDG_STATE_HOME", value)
        assert platform_snapshot_root(system="posix") == expected
