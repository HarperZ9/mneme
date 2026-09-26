"""erase_receipt.py: what an erase removed, what it checked, and what remains.

The receipt carries counts, random erase refs, the scrub result, a residual
scan of the database files, and two lists in the same place: residue inside
the store the erase could not remove, and copies outside its reach. It never
carries erased text, a content-derived id, or a path. The openings of the
erase commitments ride along only when the local CLI asks for them.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import snapshot_dir
from .erase_scan import scan_paths

RECEIPT_SCHEMA = "mneme.erase-receipt/1"
SIDECARS = ("-journal", "-wal", "-shm")
UNSALTED_OPS = ("forget", "update", "supersede")


def blinded_ords(conn: sqlite3.Connection) -> set[int]:
    """Audit rows whose hashes are salted commitments (none before schema 5)."""
    return set()


def legacy_audit_rows(conn: sqlite3.Connection, subjects) -> dict:
    """Earlier audit rows that still name an erased row by content-derived value."""
    ids = {s.id for s in subjects}
    hashes = {s.content_sha256 for s in subjects if s.content_sha256}
    long_ids = [i for i in ids if len(i) >= 12]
    blinded = blinded_ords(conn)
    count = unsalted = 0
    for ord_, op, memory_id, before, after, reason in conn.execute(
            "SELECT ord, op, memory_id, before_sha, after_sha, reason FROM audit"):
        if (memory_id in ids or before in hashes or after in hashes
                or any(i in reason for i in long_ids)):
            count += 1
            if op in UNSALTED_OPS and (before or after) and ord_ not in blinded:
                unsalted += 1
    return {"count": count, "unsalted_hashes": unsalted,
            "note": "audit rows written before this erase keep the content-derived "
                    "ids (and, before schema 5, the unsalted hashes) of the rows they "
                    "describe; the log is append-only, so they stay"}


def _db_path(conn: sqlite3.Connection) -> Path | None:
    for _seq, name, file in conn.execute("PRAGMA database_list"):
        if name == "main":
            return Path(file) if file else None
    return None


def kept_values(conn: sqlite3.Connection) -> list[str]:
    """Every string the store still holds, to tell kept text from residue."""
    values = [r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")]
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    for table in tables:
        for row in conn.execute(f'SELECT * FROM "{table}"'):
            values.extend(v for v in row if isinstance(v, str))
    return values


def _scan(db: Path | None, texts, kept) -> dict:
    if db is None:
        return {"status": "not_applicable", "note": "in-memory store: no file to scan"}
    files = [p for p in (db, *(Path(f"{db}{s}") for s in SIDECARS)) if p.exists()]
    result = scan_paths(files, texts, kept)
    result["status"] = "hits" if result["files_with_hits"] else "clean"
    return result


def _legacy_temp(texts, kept) -> dict:
    item = {"class": "legacy_temp_snapshots",
            "note": "replay snapshots that mneme before 0.5.0 left in the OS temp "
                    "directory; they cannot be tied to one store, so they are "
                    "counted here and left in place"}
    try:
        paths = snapshot_dir.legacy_temp_snapshots()
    except OSError as exc:
        return {**item, "count": None, "error": exc.__class__.__name__}
    found = scan_paths(paths, texts, kept)["files_with_hits"] if paths else []
    return {**item, "count": len(paths), "containing_erased_text": len(found)}


def _remove_snapshots(conn: sqlite3.Connection, db: Path | None) -> dict:
    try:
        removed = snapshot_dir.remove_store_snapshots(snapshot_dir.store_id_of(conn), db)
    except OSError as exc:
        return {"removed": 0, "failed": 1, "error": exc.__class__.__name__}
    return {"removed": removed["removed"], "failed": len(removed["failed"])}


def _out_of_reach(texts, kept) -> list[dict]:
    return [
        {"class": "exports", "note": "files written earlier by `mneme inspect --out` "
                                     "or `mneme to-crucible`"},
        {"class": "copies_and_backups", "note": "backups, sync copies, and any other "
                                                "copy of the database file"},
        _legacy_temp(texts, kept),
    ]


def _residue(work: dict, scan: dict, file_backed: bool) -> dict:
    residue = {"legacy_audit_rows": work["legacy"],
               "short_texts": {"count": scan.get("short_texts", 0),
                               "note": "texts under 16 bytes get structural checks only"},
               "kept_overlap": {"texts": scan.get("kept_overlap", 0),
                                "note": "erased texts that share a 16-byte run with "
                                        "rows the store keeps"}}
    if file_backed:
        residue["freed_clusters"] = {
            "note": "SQLite rewrote and shrank the database file; the disk blocks it "
                    "released and its deleted rollback journal are not overwritten and "
                    "can hold erased bytes until the file system reuses them"}
    return residue


def _rows_absent(conn: sqlite3.Connection, plan: dict) -> bool:
    for table, ids in (("memories", plan["memories"]), ("turns", plan["turns"])):
        for item in ids:
            if conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (item,)).fetchone():
                return False
    return True


def build_receipt(store, work: dict, scrub: dict, *, reason: str,
                  emit_openings: bool) -> dict:
    conn, plan = store.conn, work["plan"]
    db = _db_path(conn)
    snapshots = _remove_snapshots(conn, db)
    kept = kept_values(conn)
    scan = _scan(db, work["texts"], kept)
    structural = {"rows_absent": _rows_absent(conn, plan),
                  **{k: scrub[k] for k in ("freelist_count", "journal", "wal_bytes")}}
    clean = (scan["status"] != "hits" and structural["rows_absent"]
             and "remedy" not in scrub and not snapshots["failed"])
    receipt = {
        "schema": RECEIPT_SCHEMA, "status": "erased" if clean else "erased_residue_found",
        "plan_sha256": plan["plan_sha256"], "reason": reason,
        "keep_sources": plan["keep_sources"], "counts": plan["counts"],
        "erase_refs": [{"ref": o["erase_ref"], "layer": o["layer"]}
                       for o in work["openings"]],
        "scrub": {k: v for k, v in scrub.items() if k not in structural},
        "structural": structural, "scan": scan,
        "snapshots": snapshots,
        "residue": _residue(work, scan, db is not None),
        "out_of_reach": _out_of_reach(work["texts"], kept),
    }
    if emit_openings:
        receipt["openings"] = work["openings"]
    return receipt
