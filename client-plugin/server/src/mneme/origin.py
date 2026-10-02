"""Explicit local origin freshness checks for Mneme provenance chains.

Internal Mneme drift checks compare a memory to the source rows stored inside
Mneme. This module performs a separate, opt-in check against a caller-approved
local origin file for the Gather docs/file-read profile.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

SCHEMA = "mneme.local-origin-recheck/1"
GATHER_DOCS_FILE_READ_PROFILE = "gather.docs.file-read/v1"
MAX_ORIGIN_BYTES = 16 * 1024 * 1024

MATCH = "MATCH"
DRIFT = "DRIFT"
UNVERIFIABLE = "UNVERIFIABLE"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RAW_BYTE_LIMIT = (
    "raw byte integrity; this profile compares Gather's normalized decoded text "
    "hash, not the original file byte stream"
)
_RACE_REASON = "origin ref changed during validation/open"


@dataclass(frozen=True)
class _PathIdentity:
    path: Path
    fingerprint: tuple[int, int, int, int]
    directory: bool


@dataclass(frozen=True)
class _ValidatedOriginPath:
    path: Path
    components: tuple[_PathIdentity, ...]

    @property
    def final(self) -> _PathIdentity:
        return self.components[-1]

    @property
    def parents(self) -> tuple[_PathIdentity, ...]:
        return self.components[:-1]


def recheck_local_origins(memory, memory_id: str, *, allowed_root: str | Path,
                          profile: str = GATHER_DOCS_FILE_READ_PROFILE,
                          max_bytes: int = MAX_ORIGIN_BYTES) -> dict:
    """Re-read supported local origins for one memory under ``allowed_root``.

    The check supports only Gather local docs receipts (``source=docs`` and
    ``method=file-read``). It does not follow network refs, UNC/device paths, or
    symlink/reparse/hardlink aliases, and it does not include source content in
    the report.
    """
    max_bytes = _validate_max_bytes(max_bytes)
    allowed = _allowed_root(allowed_root)
    chain = memory.provenance_chain(memory_id)
    if chain is None:
        origins = [_unverifiable(
            origin_present=False,
            reason="no memory with requested id",
        )]
    else:
        origins = [
            _recheck_link(link, allowed_root=allowed, profile=profile,
                          max_bytes=max_bytes)
            for link in chain["chain"]
        ]
        if not origins:
            origins = [_unverifiable(
                origin_present=False,
                reason="memory has no source links to recheck",
            )]

    overall = _rollup(origins)
    checked = sum(1 for row in origins if row.get("verdict") in {MATCH, DRIFT})
    return {
        "schema": SCHEMA,
        "memory_id": memory_id,
        "profile": profile,
        "allowed_root": str(allowed),
        "overall": overall,
        "checked": checked,
        "origins": origins,
        "does_not_prove": [_RAW_BYTE_LIMIT],
    }


def _validate_max_bytes(max_bytes: int) -> int:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
        raise ValueError("max_bytes must be a positive bounded integer")
    if max_bytes <= 0 or max_bytes > MAX_ORIGIN_BYTES:
        raise ValueError("max_bytes must be a positive bounded integer")
    return max_bytes


def _allowed_root(raw: str | Path) -> Path:
    raw_path = Path(raw).expanduser()
    absolute = raw_path if raw_path.is_absolute() else Path.cwd() / raw_path
    problem = _reject_linked_or_reparse_component(absolute, label="allowed_root")
    if problem:
        raise ValueError(problem)
    try:
        root = absolute.resolve(strict=True)
    except OSError as exc:
        raise ValueError(_io_error("allowed_root is unavailable", exc)) from exc
    if not root.is_dir():
        raise ValueError("allowed_root is not a directory")
    problem = _reject_linked_or_reparse_component(root, label="allowed_root")
    if problem:
        raise ValueError(problem)
    return root


def _rollup(origins: list[dict]) -> str:
    verdicts = [row.get("verdict") for row in origins]
    if DRIFT in verdicts:
        return DRIFT
    if UNVERIFIABLE in verdicts:
        return UNVERIFIABLE
    return MATCH


def _recheck_link(link: dict, *, allowed_root: Path, profile: str,
                  max_bytes: int) -> dict:
    row = {
        "turn_id": link.get("turn_id"),
        "turn_present": bool(link.get("turn_present")),
        "profile": profile,
        "does_not_prove": [_RAW_BYTE_LIMIT],
    }
    origin = link.get("origin")
    if not isinstance(origin, dict):
        return _unverifiable(row, origin_present=False,
                             reason="source turn has no external origin receipt")

    row.update({
        "origin_present": True,
        "source": str(origin.get("source", "")),
        "method": str(origin.get("method", "")),
        "ref": str(origin.get("ref", "")),
        "origin_sha256": str(origin.get("sha256", "")),
    })
    if profile != GATHER_DOCS_FILE_READ_PROFILE:
        return _unverifiable(row, reason=f"unsupported origin recheck profile: {profile}")
    if (row["source"], row["method"]) != ("docs", "file-read"):
        return _unverifiable(
            row,
            reason=("unsupported origin source/method for local recheck: "
                    f"source={row['source']!r}, method={row['method']!r}"),
        )
    if not _SHA256.match(row["origin_sha256"]):
        return _unverifiable(row, reason="origin sha256 is not a lowercase SHA-256")

    path_result = _safe_origin_path(row["ref"], allowed_root)
    if isinstance(path_result, str):
        return _unverifiable(row, reason=path_result)

    read_result = _read_gather_docs_text(path_result, max_bytes=max_bytes)
    if isinstance(read_result, str):
        return _unverifiable(row, reason=read_result)
    text, bytes_read = read_result
    current = hashlib.sha256(text.encode("utf-8")).hexdigest()
    row.update({
        "comparison": "normalized_text_sha256",
        "current_sha256": current,
        "bytes_read": bytes_read,
    })
    if current != row["origin_sha256"]:
        row.update({
            "verdict": DRIFT,
            "reason": "normalized text hash differs from origin receipt",
        })
    else:
        row.update({
            "verdict": MATCH,
            "reason": "normalized text hash matches origin receipt",
        })
    return row


def _safe_origin_path(ref: str, allowed_root: Path) -> _ValidatedOriginPath | str:
    if not ref:
        return "origin ref is empty"
    if _is_unc_or_device_path(ref):
        return "network/UNC/device paths are not supported"
    if _looks_like_url(ref):
        return "network and URL origin refs are not supported"
    raw = Path(ref).expanduser()
    if not raw.is_absolute():
        return "origin ref must be an absolute local path"
    resolved = raw.resolve(strict=False)
    try:
        resolved.relative_to(allowed_root)
    except ValueError:
        return "origin ref is outside allowed root"
    if not raw.exists():
        return "origin ref is missing"
    component_problem = _reject_linked_or_reparse_component(raw, label="origin ref")
    if component_problem:
        return component_problem
    try:
        components = _component_identities(allowed_root, resolved)
    except OSError as exc:
        return _io_error("origin ref is unreadable", exc)
    final = components[-1]
    try:
        st = final.path.lstat()
    except OSError as exc:
        return _io_error("origin ref is unreadable", exc)
    if not stat.S_ISREG(st.st_mode):
        return "origin ref is not a regular file"
    if getattr(st, "st_nlink", 1) > 1:
        return "origin ref is a hardlink alias"
    return _ValidatedOriginPath(path=resolved, components=tuple(components))


def _is_unc_or_device_path(ref: str) -> bool:
    return ref.startswith(("\\\\", "//")) or ref.startswith(("\\\\.\\", "\\\\?\\"))


def _looks_like_url(ref: str) -> bool:
    parsed = urlparse(ref)
    if not parsed.scheme:
        return False
    # urlparse treats Windows drive letters such as C:\tmp\a.txt as schemes.
    return not re.match(r"^[A-Za-z]:[\\/]", ref)


def _component_identities(root: Path, path: Path) -> list[_PathIdentity]:
    relative = path.relative_to(root)
    current = root
    out = [_identity(current, directory=True)]
    for index, part in enumerate(relative.parts):
        current = current / part
        out.append(_identity(current, directory=index < len(relative.parts) - 1))
    return out


def _identity(path: Path, *, directory: bool) -> _PathIdentity:
    st = path.lstat()
    if directory and not stat.S_ISDIR(st.st_mode):
        raise OSError("not a directory")
    return _PathIdentity(path=path, fingerprint=_fingerprint(st), directory=directory)


def _fingerprint(st) -> tuple[int, int, int, int]:
    return (
        int(getattr(st, "st_dev", 0)),
        int(getattr(st, "st_ino", 0)),
        int(st.st_mode),
        int(getattr(st, "st_file_attributes", 0)),
    )


def _reject_linked_or_reparse_component(path: Path, *, label: str) -> str | None:
    current = Path(path.anchor)
    parts = list(path.parts)
    for part in (parts[1:] if path.anchor else parts):
        current = current / part
        if not current.exists():
            return None
        try:
            st = current.lstat()
        except OSError as exc:
            return _io_error(f"{label} is unreadable", exc)
        if current.is_symlink():
            return f"{label} traverses a symlink"
        attributes = getattr(st, "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            return f"{label} traverses a reparse point"
    return None


def _ensure_components_unchanged(validated: _ValidatedOriginPath) -> str | None:
    for component in validated.components:
        try:
            st = component.path.lstat()
        except OSError:
            return _RACE_REASON
        if _fingerprint(st) != component.fingerprint:
            return _RACE_REASON
        if component.path.is_symlink():
            return _RACE_REASON
        attributes = getattr(st, "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            return _RACE_REASON
    return None


def _read_gather_docs_text(validated: _ValidatedOriginPath, *, max_bytes: int) -> tuple[str, int] | str:
    parent_handles = []
    fd = None
    try:
        changed = _ensure_components_unchanged(validated)
        if changed:
            return changed
        hold_result = _hold_parent_chain(validated)
        if isinstance(hold_result, str):
            return hold_result
        parent_handles = hold_result
        changed = _ensure_components_unchanged(validated)
        if changed:
            return changed
        open_result = _open_validated_file(validated, parent_handles)
        if isinstance(open_result, str):
            return open_result
        fd = open_result
        try:
            st = os.fstat(fd)
        except OSError as exc:
            return _io_error("origin ref is unreadable", exc)
        if _fingerprint(st) != validated.final.fingerprint:
            return _RACE_REASON
        if not stat.S_ISREG(st.st_mode):
            return "origin ref is not a regular file"
        if getattr(st, "st_nlink", 1) > 1:
            return "origin ref is a hardlink alias"
        changed = _ensure_components_unchanged(validated)
        if changed:
            return changed
        read_result = _read_bounded_fd(fd, max_bytes=max_bytes)
    finally:
        if fd is not None:
            os.close(fd)
        _close_parent_chain(parent_handles)
    if isinstance(read_result, str):
        return read_result
    data = read_result
    try:
        data.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return "invalid UTF-8 for gather.docs.file-read/v1; lossy replacement is unverifiable"
    text = io.TextIOWrapper(
        io.BytesIO(data),
        encoding="utf-8",
        errors="replace",
        newline=None,
    ).read()
    return text, len(data)


def _read_bounded_fd(fd: int, *, max_bytes: int) -> bytes | str:
    remaining = max_bytes + 1
    chunks = []
    total = 0
    while remaining > 0:
        try:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
        except OSError as exc:
            return _io_error("origin ref is unreadable", exc)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        remaining -= len(chunk)
        if total > max_bytes:
            return f"origin ref exceeds maximum read size of {max_bytes} bytes"
    return b"".join(chunks)


def _open_validated_file(validated: _ValidatedOriginPath, parent_handles) -> int | str:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    if os.name == "posix":
        flags |= getattr(os, "O_NONBLOCK", 0)
    if os.name == "posix" and parent_handles and os.open in getattr(os, "supports_dir_fd", set()):
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            return os.open(validated.path.name, flags, dir_fd=parent_handles[-1])
        except OSError as exc:
            return _io_error("origin ref is unreadable", exc)
    try:
        return os.open(validated.path, flags)
    except OSError as exc:
        return _io_error("origin ref is unreadable", exc)


def _hold_parent_chain(validated: _ValidatedOriginPath):
    parents = validated.parents
    if not parents:
        return []
    if os.name == "posix":
        return _hold_parent_chain_posix(parents)
    if os.name == "nt":
        return _hold_parent_chain_windows(parents)
    return "origin ref cannot be safely held on this platform"


def _hold_parent_chain_posix(parents: tuple[_PathIdentity, ...]):
    handles = []
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        for parent in parents:
            fd = os.open(parent.path, flags)
            handles.append(fd)
            st = os.fstat(fd)
            if _fingerprint(st) != parent.fingerprint:
                _close_parent_chain(handles)
                return _RACE_REASON
        return handles
    except OSError as exc:
        _close_parent_chain(handles)
        return _io_error("origin ref parent is unavailable", exc)


def _hold_parent_chain_windows(parents: tuple[_PathIdentity, ...]):
    try:
        import ctypes
    except Exception:
        return "origin ref cannot be safely held on this platform"

    kernel32 = ctypes.windll.kernel32
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    invalid = ctypes.c_void_p(-1).value
    file_read_attributes = 0x0080
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    handles = []
    try:
        for parent in parents:
            handle = create_file(
                str(parent.path),
                file_read_attributes,
                file_share_read | file_share_write,
                None,
                open_existing,
                file_flag_backup_semantics | file_flag_open_reparse_point,
                None,
            )
            if handle == invalid or handle is None:
                _close_parent_chain(handles)
                return "origin ref parent is unavailable"
            handles.append(("win", handle))
            try:
                st = parent.path.lstat()
            except OSError:
                _close_parent_chain(handles)
                return _RACE_REASON
            if _fingerprint(st) != parent.fingerprint:
                _close_parent_chain(handles)
                return _RACE_REASON
        return handles
    except Exception:
        _close_parent_chain(handles)
        return "origin ref parent is unavailable"


def _close_parent_chain(handles) -> None:
    if not handles:
        return
    if os.name == "nt":
        try:
            import ctypes
            close_handle = ctypes.windll.kernel32.CloseHandle
        except Exception:
            close_handle = None
        for item in reversed(handles):
            if isinstance(item, tuple) and item[0] == "win":
                if close_handle is not None:
                    close_handle(ctypes.c_void_p(item[1]))
            else:
                try:
                    os.close(item)
                except OSError:
                    pass
        return
    for fd in reversed(handles):
        try:
            os.close(fd)
        except OSError:
            pass


def _io_error(label: str, exc: OSError) -> str:
    name = exc.__class__.__name__
    return f"{label}: {name}"


def _unverifiable(base: dict | None = None, *, origin_present: bool | None = None,
                  reason: str) -> dict:
    row = dict(base or {})
    if origin_present is not None:
        row["origin_present"] = origin_present
    row.update({"verdict": UNVERIFIABLE, "reason": reason})
    row.setdefault("does_not_prove", [_RAW_BYTE_LIMIT])
    return row
