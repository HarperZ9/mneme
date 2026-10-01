"""erase_outside.py: copies of the store that an erase cannot remove.

Some copies can only be named (`named_only`): exports, backups and other
copies of the file, text that reached a model provider (an LLM extractor or
embedder, or an MCP client that read recall, provenance or plan previews), and
the MCP client's own session history, which keeps every result it read on
the owner's machine. Two kinds can also be scanned, and a hit keeps the
receipt from saying `erased`:

- replay snapshots that mneme before 0.5.0 left in the OS temp directory,
  which cannot be tied to one store, so they are scanned and left in place;
- snapshots of other stores under the snapshot root, which may be copies of
  this store made under another path or id.

Both are foreign files. A link is never listed as a snapshot, and no file
is read past `MAX_COPY_BYTES`. On POSIX a file is also opened without
following a link and without blocking, and skipped when another user owns
it; Windows has neither the flags nor file owner ids in this form, so those
two checks do not run there.
"""
from __future__ import annotations

from pathlib import Path

from . import snapshot_dir
from .erase_scan import scan_paths
from .snapshot_binding import bound_root

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
    if bound_root() is not None:
        return {"class": "legacy_temp_snapshots", "count": None, "not_scanned": 1,
                "note": "outside this client's selected snapshot authority; not inspected or modified"}
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


def named_only() -> list[dict]:
    """The copies an erase can name but neither scan nor remove."""
    return [
        {"class": "exports", "note": "files written earlier by `mneme inspect --out` "
                                     "or `mneme to-crucible`"},
        {"class": "copies_and_backups", "note": "backups, sync copies, and any other "
                                                "copy of the database file"},
        {"class": "model_providers",
         "note": "text sent earlier to a model provider: turns given to an LLM "
                 "extractor or embedder, and results an MCP client read (recall, "
                 "provenance, forget previews); the provider's terms govern them"},
        {"class": "client_transcripts",
         "note": "the MCP client's own session history on this machine, which keeps "
                 "every result it read (recall, provenance, plan previews, audit, "
                 "Crucible exports); delete it in that client"},
    ]


def out_of_reach(store_id, db: Path | None, texts, kept) -> list[dict]:
    items = [*named_only(), _legacy_temp(texts, kept),
             _other_snapshots(store_id, db, texts, kept)]
    if bound_root() is not None:
        items.append({"class": "global_snapshots", "count": None, "not_scanned": 1,
                      "note": "legacy global snapshot directories are outside this client's authority"})
    return items


def copies_found(items: list[dict]) -> tuple[int, int]:
    """(copies holding erased text, copies that could not be checked)."""
    held = sum(i.get("containing_erased_text") or 0 for i in items)
    unchecked = sum(i.get("not_scanned") or 0 for i in items)
    return held, unchecked
