"""snapshot_dir.py: where replay snapshots live, and how they are found again.

A replay snapshot is a full copy of a memory database. Mneme used to create it
in the OS temp directory and remove it best effort, so a crash left an
unregistered copy there. Snapshots now live in a per-user state directory:
`<LocalAppData>/mneme/snapshots` on Windows (read through the Known Folder
API, not an environment variable), `$XDG_STATE_HOME/mneme/snapshots` or
`~/.local/state/mneme/snapshots` elsewhere. They never sit beside the
database, whose default location is the current working directory.

Each writable store carries a random `store_id` in meta, and its snapshots go
under `<root>/<store_id>/`, so an erase can find and remove every copy of the
store it just changed. A source without a valid id (read-only, legacy, or a
hostile value) maps to `<root>/unkeyed/<sha256 of its resolved path>/`. File
names carry the creating process id; a name whose id is out of range is
`unknown` and never swept. Snapshots whose process is gone are swept when a
writable store opens, when the MCP server starts, when mneme creates a
snapshot, and when an erase starts. A directory that cannot be listed raises
OSError rather than reading as empty, so an erase counts a failed removal.
A link is never listed as a snapshot, and a linked snapshot directory is
refused.

On POSIX an unlink succeeds while another process holds the file open, and
that process can keep reading it. A snapshot removed while its creating
process still runs is therefore reported as a copy that may remain.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import secrets
import sqlite3
import stat
import logging
import tempfile
from pathlib import Path

from .audit_writer import meta_get, meta_set
from .os_facts import known_local_appdata as _known_local_appdata
from .os_facts import pid_alive as _pid_alive
from .schema import META_STORE_ID
from .snapshot_binding import bound_root, check_bound_path

STORE_ID_PATTERN = r"st_[0-9a-f]{32}"
SNAPSHOT_GLOB = "mneme-replay-*.db"
SIDECARS = ("-journal", "-wal", "-shm")
_NAME = re.compile(r"mneme-replay-(\d{1,10})-[A-Za-z0-9_]+\.db")
MAX_PID = 2**31 - 1                  # a pid_t on POSIX; Windows ids stay far below
UNLINK_KEEPS_OPEN_FILES = os.name != "nt"
_LOG = logging.getLogger("mneme")


def new_store_id() -> str:
    return "st_" + secrets.token_hex(16)


def valid_store_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(STORE_ID_PATTERN, value) is not None


def store_id_of(conn: sqlite3.Connection) -> str | None:
    """The store's id when present and well formed; None for any other value."""
    try:
        value = meta_get(conn, META_STORE_ID)
    except sqlite3.Error:
        return None
    return value if valid_store_id(value) else None


def ensure_store_id(conn: sqlite3.Connection) -> str:
    """Give a writable store an id, replacing a malformed one. No commit."""
    current = store_id_of(conn)
    if current is not None:
        return current
    fresh = new_store_id()
    meta_set(conn, META_STORE_ID, fresh)
    return fresh


def platform_snapshot_root(*, system: str | None = None) -> Path:
    """The per-user snapshot root for this platform (`system` defaults to os.name)."""
    if (system or os.name) == "nt":
        try:
            base = _known_local_appdata()
        except (OSError, AttributeError):
            base = Path.home() / "AppData" / "Local"
    else:
        xdg = os.environ.get("XDG_STATE_HOME", "")
        base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".local" / "state"
    return base / "mneme" / "snapshots"


def snapshot_root() -> Path:
    return bound_root() or platform_snapshot_root()


def _path_key(path: Path) -> str:
    resolved = os.path.normcase(str(Path(path).resolve()))
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()


def store_dir(store_id: str | None, source_path: Path | None) -> Path | None:
    root = snapshot_root()
    if valid_store_id(store_id):
        return root / store_id
    if source_path is None:
        return None
    return root / "unkeyed" / _path_key(source_path)


def _is_link(path: Path) -> bool:
    info = os.lstat(path)
    reparse = getattr(info, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(reparse)


def _private_dir(path: Path) -> Path:
    root = snapshot_root()
    if root not in path.parents:
        raise OSError("snapshot directory is outside the snapshot root")
    check_bound_path(path)
    path.mkdir(parents=True, exist_ok=True)
    for part in [path, *path.parents]:
        if part == root.parent:
            break
        if _is_link(part) or not part.is_dir():
            raise OSError(f"snapshot directory {part.name!r} is a link or not a directory")
        if os.name != "nt":
            if os.stat(part).st_uid != os.getuid():
                raise OSError(f"snapshot directory {part.name!r} is not owned by this user")
            os.chmod(part, 0o700)
    return path


def _read_source_store_id(source_path: Path) -> str | None:
    try:
        conn = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True, timeout=1.0)
    except sqlite3.Error:
        return None
    try:
        return store_id_of(conn)
    finally:
        conn.close()


def create_snapshot_file(source_path: Path) -> tuple[int, str]:
    """Create an empty snapshot file for `source_path` in its store directory."""
    directory = _private_dir(store_dir(_read_source_store_id(source_path), source_path))
    sweep_orphans()
    return tempfile.mkstemp(prefix=f"mneme-replay-{os.getpid()}-", suffix=".db",
                            dir=directory)


def _remove_with_sidecars(path: Path) -> bool:
    ok = True
    for candidate in (path, *(Path(f"{path}{s}") for s in SIDECARS)):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            ok = False
    return ok


def _snapshot_files(directory: Path) -> list[Path]:
    """Regular files named like a snapshot. A missing directory is empty; one
    that cannot be listed raises (Path.glob would read it as empty)."""
    check_bound_path(directory)
    try:
        entries = list(os.scandir(directory))
    except (FileNotFoundError, NotADirectoryError):
        return []
    return sorted(Path(e.path) for e in entries
                  if fnmatch.fnmatch(e.name, SNAPSHOT_GLOB)
                  and e.is_file(follow_symlinks=False))


def _store_dirs(root: Path) -> list[Path]:
    check_bound_path(root)
    if not root.is_dir():
        return []
    dirs = [d for d in root.iterdir() if d.is_dir() and valid_store_id(d.name)]
    unkeyed = root / "unkeyed"
    check_bound_path(unkeyed)
    if unkeyed.is_dir():
        dirs += [d for d in unkeyed.iterdir() if d.is_dir()]
    return dirs


def _pid_of(path: Path) -> int | None:
    """The creating process id in a snapshot name, or None when absent or out of
    range (a value the OS would truncate or refuse)."""
    match = _NAME.fullmatch(path.name)
    if match is None:
        return None
    pid = int(match.group(1))
    return pid if 0 < pid <= MAX_PID else None


def _state_of(path: Path) -> str:
    """'live' or 'orphaned' by the creating process id in the name, else 'unknown'."""
    pid = _pid_of(path)
    if pid is None:
        return "unknown"
    return "live" if _pid_alive(pid) else "orphaned"


def sweep_orphans(root: Path | None = None) -> dict:
    """Remove snapshots whose creating process is gone. Unparseable names stay."""
    counts = {"removed": 0, "kept_live": 0, "kept_unknown": 0, "failed": 0}
    for directory in _store_dirs(root or snapshot_root()):
        for path in _snapshot_files(directory):
            state = _state_of(path)
            if state != "orphaned":
                counts[f"kept_{state}"] += 1
            elif _remove_with_sidecars(path):
                counts["removed"] += 1
            else:
                counts["failed"] += 1
    return counts


def startup_sweep() -> dict | None:
    """Sweep orphans; a failure is logged, never raised, so opening a store works."""
    try:
        return sweep_orphans()
    except Exception as exc:          # a hostile name or directory must not stop an open
        _LOG.warning("mneme: could not sweep orphaned replay snapshots: %s: %s",
                     exc.__class__.__name__, exc)
        return None


def _directories(store_id: str | None, source_path: Path | None) -> list[Path]:
    """The keyed directory and the by-path directory a store's snapshots can use."""
    found = {store_dir(store_id, None), store_dir(None, source_path)}
    return sorted(d for d in found if d is not None)


def store_snapshot_counts(store_id: str | None, source_path: Path | None) -> dict:
    """Count this store's snapshots by state, without removing any."""
    counts = {"live": 0, "orphaned": 0, "unknown": 0}
    for directory in _directories(store_id, source_path):
        for path in _snapshot_files(directory):
            counts[_state_of(path)] += 1
    return counts


def _held_elsewhere(path: Path) -> bool:
    pid = _pid_of(path)
    return (UNLINK_KEEPS_OPEN_FILES and pid is not None
            and pid != os.getpid() and _pid_alive(pid))


def remove_store_snapshots(store_id: str | None, source_path: Path | None) -> dict:
    """Remove every snapshot of this store, live or not, keyed or by path.

    `removed_while_live` counts removed snapshots whose creating process still
    runs, where an open handle can outlive the unlink."""
    removed, failed, live = 0, [], 0
    for directory in _directories(store_id, source_path):
        for path in _snapshot_files(directory):
            held = _held_elsewhere(path)
            if _remove_with_sidecars(path):
                removed += 1
                live += held
            else:
                failed.append(path)
    return {"removed": removed, "failed": failed, "removed_while_live": live}


def other_snapshots(store_id: str | None, source_path: Path | None) -> list[Path]:
    """Snapshots under the root that belong to other stores."""
    own = set(_directories(store_id, source_path))
    return [path for directory in _store_dirs(snapshot_root()) if directory not in own
            for path in _snapshot_files(directory)]


def legacy_temp_snapshots() -> list[Path]:
    """Snapshots that mneme before 0.5.0 left in the OS temp directory."""
    if bound_root() is not None:
        return []  # A bound client has no authority over the shared legacy store.
    return _snapshot_files(Path(tempfile.gettempdir()))
