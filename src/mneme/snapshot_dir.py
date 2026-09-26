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
names carry the creating process id, and whenever mneme creates a snapshot it
first sweeps snapshots whose process is gone.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import stat
import tempfile
from pathlib import Path

from .audit_writer import meta_get, meta_set
from .schema import META_STORE_ID

STORE_ID_PATTERN = r"st_[0-9a-f]{32}"
SNAPSHOT_GLOB = "mneme-replay-*.db"
SIDECARS = ("-journal", "-wal", "-shm")
_NAME = re.compile(r"mneme-replay-(\d+)-[A-Za-z0-9_]+\.db")


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


def _known_local_appdata() -> Path:
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    local_appdata = GUID(0xF1B32785, 0x6FBA, 0x4FCF, (ctypes.c_ubyte * 8)(
        0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91))
    get_path = ctypes.windll.shell32.SHGetKnownFolderPath
    get_path.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                         ctypes.POINTER(ctypes.c_wchar_p)]
    get_path.restype = ctypes.c_long
    out = ctypes.c_wchar_p()
    if get_path(ctypes.byref(local_appdata), 0, None, ctypes.byref(out)) != 0:
        raise OSError("SHGetKnownFolderPath(LocalAppData) failed")
    try:
        return Path(out.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))


def platform_snapshot_root() -> Path:
    """The per-user snapshot root for this platform."""
    if os.name == "nt":
        try:
            base = _known_local_appdata()
        except (OSError, AttributeError):
            base = Path.home() / "AppData" / "Local"
    else:
        xdg = os.environ.get("XDG_STATE_HOME", "")
        base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".local" / "state"
    return base / "mneme" / "snapshots"


def snapshot_root() -> Path:
    return platform_snapshot_root()


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


def _pid_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5                # access denied: it exists
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == 259                             # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


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
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob(SNAPSHOT_GLOB)
                  if p.is_file() and not p.is_symlink())


def _store_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    dirs = [d for d in root.iterdir() if d.is_dir() and valid_store_id(d.name)]
    unkeyed = root / "unkeyed"
    if unkeyed.is_dir():
        dirs += [d for d in unkeyed.iterdir() if d.is_dir()]
    return dirs


def _state_of(path: Path) -> str:
    """'live' or 'orphaned' by the creating process id in the name, else 'unknown'."""
    match = _NAME.fullmatch(path.name)
    if match is None:
        return "unknown"
    return "live" if _pid_alive(int(match.group(1))) else "orphaned"


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


def remove_store_snapshots(store_id: str | None, source_path: Path | None) -> dict:
    """Remove every snapshot of this store, live or not, keyed or by path."""
    removed, failed = 0, []
    for directory in _directories(store_id, source_path):
        for path in _snapshot_files(directory):
            if _remove_with_sidecars(path):
                removed += 1
            else:
                failed.append(path)
    return {"removed": removed, "failed": failed}


def legacy_temp_snapshots() -> list[Path]:
    """Snapshots that mneme before 0.5.0 left in the OS temp directory."""
    return _snapshot_files(Path(tempfile.gettempdir()))
