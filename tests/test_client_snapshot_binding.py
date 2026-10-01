"""Bound client snapshots cannot sweep another store or the legacy global root."""
from pathlib import Path
import os
import subprocess
import pytest

from mneme import snapshot_dir
from mneme.snapshot_binding import for_state


def test_bound_erase_keeps_uninspected_copies_unknown(tmp_path):
    from mneme.erase_outside import out_of_reach, copies_found
    with for_state(tmp_path / 'memory.db'):
        items = out_of_reach(None, tmp_path / 'memory.db', [], [])
    by_class = {item['class']: item for item in items}
    assert by_class['legacy_temp_snapshots']['count'] is None
    assert by_class['global_snapshots']['count'] is None
    assert copies_found(items) == (0, 2)


def test_bound_sweep_and_legacy_defaults(tmp_path, monkeypatch, snapshot_root):
    outside = snapshot_root / ('st_' + 'a' * 32)
    outside.mkdir(parents=True)
    sentinel = outside / 'mneme-replay-123-fixture.db'
    sentinel.write_bytes(b'global snapshot')
    monkeypatch.setattr(snapshot_dir, '_pid_alive', lambda pid: False)
    with for_state(tmp_path / 'first.db'):
        first = snapshot_dir.snapshot_root()
        inside = first / ('st_' + 'b' * 32)
        inside.mkdir(parents=True)
        orphan = inside / 'mneme-replay-123-fixture.db'
        orphan.write_bytes(b'bound snapshot')
        assert snapshot_dir.startup_sweep()['removed'] == 1
        assert not orphan.exists()
        assert sentinel.read_bytes() == b'global snapshot'
        assert snapshot_dir.legacy_temp_snapshots() == []
        with for_state(tmp_path / 'second.db'):
            assert snapshot_dir.snapshot_root() != first
        assert snapshot_dir.snapshot_root() == first
    assert snapshot_dir.snapshot_root() == snapshot_root


@pytest.mark.parametrize('nested', [False, True])
def test_bound_root_link_refused_before_sweep(tmp_path, snapshot_root, nested):
    target = tmp_path / 'outside'; target.mkdir()
    with for_state(tmp_path / 'bound.db'):
        root = snapshot_dir.snapshot_root()
        if nested:
            root.mkdir()
            root = root / ('st_' + 'c' * 32)
        try:
            root.symlink_to(target, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                raise
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(root), str(target)], capture_output=True)
            assert result.returncode == 0
        with pytest.raises(ValueError, match='linked'):
            if nested:
                snapshot_dir.sweep_orphans()
            else:
                snapshot_dir.snapshot_root()
