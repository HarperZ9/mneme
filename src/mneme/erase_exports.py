"""erase_exports.py: a registry of the files mneme exported, so an erase finds them.

`mneme inspect --out` and `mneme replay-crucible --out` write a copy of memory
text, ids or digests to a file the owner names. Before schema 6 nothing recorded
where, so a forget left those copies in place and could only name them.
`record` now appends the file's absolute path, size and SHA-256 to
`exports.jsonl` in the store's snapshot directory (snapshot_dir.py), before
the file is written. The registry sits outside the database because a replay
reads the database through a read-only snapshot and must not change it.

`purge` runs after an erase commits. For every registered file that still
exists it looks for an erased row: an erased id or digest of 12 characters or
more, or an erased text of 8 characters or more, raw, HTML-escaped or
JSON-escaped (the forms the two exporters write). A file that holds one and
still has the digest mneme recorded is overwritten and removed (wipe.py): it
is byte for byte the copy mneme wrote. A file that holds one and has changed
since is left in place, because it is no longer mneme's file to delete; the
receipt counts it, the path goes to the log on stderr, and the status is not
`erased`. `keep=True` (CLI `--keep-exports`) leaves every file and reports the
ones that hold an erased row the same way.

Out of reach: exports written before the registry existed, output a shell
redirected to a file, and a registered file that was moved or copied.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
from pathlib import Path

from . import snapshot_dir, wipe

REGISTRY_NAME = "exports.jsonl"
MAX_EXPORT_BYTES = 1 << 30
MIN_ID, MIN_TEXT = 12, 8
_LOG = logging.getLogger("mneme")


def _registry(store_id: str | None, source_path: Path | None) -> Path | None:
    directory = snapshot_dir.store_dir(store_id, source_path)
    return directory / REGISTRY_NAME if directory is not None else None


def _registries(store_id: str | None, source_path: Path | None) -> list[Path]:
    found = []
    for directory in snapshot_dir.store_dirs_of(store_id, source_path):
        path = directory / REGISTRY_NAME
        if path.is_file() and not path.is_symlink():
            found.append(path)
    return found


def record(store_id: str | None, source_path: Path | None, out_path: Path,
           payload: bytes, kind: str) -> bool:
    """Register an export before it is written. False when the store has no
    snapshot directory (an in-memory store with no id). Raises OSError when the
    registry cannot be written, so an export never goes out unrecorded."""
    registry = _registry(store_id, source_path)
    if registry is None:
        return False
    snapshot_dir.private_dir(registry.parent)
    line = json.dumps({"path": str(Path(out_path).resolve()), "kind": kind,
                       "bytes": len(payload),
                       "sha256": hashlib.sha256(payload).hexdigest()},
                      ensure_ascii=False)
    with open(registry, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return True


def record_for(conn, state: str, out_path, payload: bytes, kind: str) -> bool:
    """`record` for the store behind `conn`, whose database path is `state`."""
    source = None if str(state) == ":memory:" else Path(state)
    return record(snapshot_dir.store_id_of(conn), source, Path(out_path), payload, kind)


def _read(registry: Path) -> list[dict]:
    entries = []
    for line in registry.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            entries.append(entry)
    return entries


def entries(store_id: str | None, source_path: Path | None) -> list[dict]:
    """Every registered export of this store, oldest first."""
    return [entry for registry in _registries(store_id, source_path)
            for entry in _read(registry)]


def _needles(identifiers, texts) -> list[bytes]:
    needles = {str(i).encode("utf-8") for i in identifiers if len(str(i)) >= MIN_ID}
    for text in texts:
        if len(text) < MIN_TEXT:
            continue
        for form in (text, html.escape(text), json.dumps(text)[1:-1],
                     json.dumps(text, ensure_ascii=False)[1:-1]):
            needles.add(form.encode("utf-8", "surrogatepass"))
    return sorted(needles)


def _load(path: Path) -> bytes | None:
    """The file's bytes, or None when it cannot be checked."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_EXPORT_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _settle(entry: dict, needles: list[bytes], keep: bool, summary: dict) -> bool:
    """Handle one registered file. True when its registry entry should stay."""
    path = Path(entry["path"])
    if not os.path.lexists(path):
        summary["missing"] += 1
        return False
    data = _load(path)
    if data is None:
        summary["unchecked"] += 1
        return True
    if not any(needle in data for needle in needles):
        return True
    unchanged = hashlib.sha256(data).hexdigest() == entry.get("sha256")
    if unchanged and not keep:
        try:
            wipe.overwrite_and_unlink(path)
            summary["removed"] += 1
            return False
        except OSError:
            pass
    summary["holding_erased_rows"] += 1
    _LOG.warning("mneme: this export still holds an erased row; delete it yourself: %s",
                 path)
    return True


def _rewrite(registry: Path, kept: list[dict]) -> None:
    if not kept:
        wipe.overwrite_and_unlink(registry)
        return
    staged = registry.with_name(registry.name + ".new")
    staged.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in kept),
                      encoding="utf-8", newline="\n")
    os.replace(staged, registry)


def purge(store_id: str | None, source_path: Path | None, identifiers, texts, *,
          keep: bool = False) -> dict:
    """Remove the registered exports that hold an erased row. Never raises."""
    summary = {"registered": 0, "removed": 0, "holding_erased_rows": 0,
               "unchecked": 0, "missing": 0}
    needles = _needles(identifiers, texts)
    try:
        for registry in _registries(store_id, source_path):
            listed = _read(registry)
            summary["registered"] += len(listed)
            kept = [entry for entry in listed if _settle(entry, needles, keep, summary)]
            if len(kept) != len(listed):
                _rewrite(registry, kept)
    except Exception as exc:          # a registry that cannot be read is unchecked
        _LOG.warning("mneme: could not check the export registry: %s",
                     exc.__class__.__name__)
        summary["unchecked"] += 1
    return summary
