"""erase_outside.py: copies of the store that an erase cannot remove.

Some copies can only be named: exports, backups and other copies of the
file, and text that reached a model provider (an LLM extractor or embedder,
or an MCP client that read recall, provenance or plan previews). Two kinds
can also be scanned, and a hit keeps the receipt from saying `erased`:

- replay snapshots that mneme before 0.5.0 left in the OS temp directory,
  which cannot be tied to one store, so they are scanned and left in place;
- snapshots of other stores under the snapshot root, which may be copies of
  this store made under another path or id.

Both are foreign files, so they are opened without following links and
without blocking, skipped when another user owns them, and not read past
`MAX_COPY_BYTES`.
"""
from __future__ import annotations

from pathlib import Path

from . import snapshot_dir
from .erase_scan import scan_paths

MAX_COPY_BYTES = 1 << 30


def _scanned(item: dict, paths, texts, kept) -> dict:
    if not paths:
        return {**item, "count": 0, "containing_erased_text": 0, "not_scanned": 0,
                "skipped": 0}
    result = scan_paths(paths, texts, kept, foreign=True, max_bytes=MAX_COPY_BYTES)
    return {**item, "count": len(paths),
            "containing_erased_text": len(result["files_with_hits"]),
            "not_scanned": len(result["not_scanned"]) + len(result["unreadable"]),
            "skipped": len(result["skipped"])}


def _legacy_temp(texts, kept) -> dict:
    item = {"class": "legacy_temp_snapshots",
            "note": "replay snapshots that mneme before 0.5.0 left in the OS temp "
                    "directory; they cannot be tied to one store, so they are "
                    "scanned, counted here and left in place"}
    try:
        paths = snapshot_dir.legacy_temp_snapshots()
    except OSError as exc:
        return {**item, "count": None, "not_scanned": 1, "error": exc.__class__.__name__}
    return _scanned(item, paths, texts, kept)


def _other_snapshots(store_id, db: Path | None, texts, kept) -> dict:
    item = {"class": "other_snapshots",
            "note": "replay snapshots of other stores under the snapshot root, which "
                    "may be copies of this store under another path or id"}
    try:
        paths = snapshot_dir.other_snapshots(store_id, db)
    except OSError as exc:
        return {**item, "count": None, "not_scanned": 1, "error": exc.__class__.__name__}
    return _scanned(item, paths, texts, kept)


def out_of_reach(store_id, db: Path | None, texts, kept) -> list[dict]:
    return [
        {"class": "exports", "note": "files written earlier by `mneme inspect --out` "
                                     "or `mneme to-crucible`"},
        {"class": "copies_and_backups", "note": "backups, sync copies, and any other "
                                                "copy of the database file"},
        {"class": "model_providers",
         "note": "text sent earlier to a model provider: turns given to an LLM "
                 "extractor or embedder, and results an MCP client read (recall, "
                 "provenance, forget previews); the provider's terms govern them"},
        _legacy_temp(texts, kept),
        _other_snapshots(store_id, db, texts, kept),
    ]


def copies_found(items: list[dict]) -> tuple[int, int]:
    """(copies holding erased text, copies that could not be checked)."""
    held = sum(i.get("containing_erased_text") or 0 for i in items)
    unchecked = sum(i.get("not_scanned") or 0 for i in items)
    return held, unchecked
