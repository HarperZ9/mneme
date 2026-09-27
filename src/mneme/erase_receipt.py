"""erase_receipt.py: what an erase removed, what it checked, and what remains.

The receipt carries counts, random erase refs, the scrub result, a residual
scan of the database files, the residue inside the store the erase could not
remove (erase_residue.py), and the copies outside its reach
(erase_outside.py). It never carries erased text, a content-derived id or a
path. The openings of the erase commitments ride along only when the caller
asks for them (the CLI's --emit-opening; the MCP tool never does).

`status` is `erased` only when every check passed:

- `erased_residue_found`: rows still present, a scrub step failed, a
  snapshot could not be removed, the scan found erased bytes, a kept row or
  an earlier audit reason repeats an erased text, or a merged-away
  duplicate's source cannot be found;
- `erased_copies_remain`: a known copy outside the store (a legacy temp
  snapshot, another store's snapshot, a snapshot removed while its process
  still ran) holds erased text;
- `incomplete`: a store file or a known copy could not be read;
- `erased_sources_kept`: the only copies left are the source turns the
  caller chose to keep.

`findings` lists every reason. A failure after the commit gives
`erased_unverified` instead (erase.py).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import erase_outside, erase_residue, snapshot_dir
from .erase_scan import scan_paths

RECEIPT_SCHEMA = "mneme.erase-receipt/1"
SIDECARS = ("-journal", "-wal", "-shm")
NOT_KEPT = ("audit", "salts", "meta")
ORDER = ("erased_residue_found", "erased_copies_remain", "incomplete",
         "erased_sources_kept")


def db_path(conn: sqlite3.Connection) -> Path | None:
    for _seq, name, file in conn.execute("PRAGMA database_list"):
        if name == "main":
            return Path(file) if file else None
    return None


def kept_values(conn: sqlite3.Connection) -> list[str]:
    """Strings of the rows the store keeps, to tell kept text from residue.

    Audit reasons, salts and meta are left out: text found there is residue."""
    values = [r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")]
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    for table in tables:
        if table in NOT_KEPT:
            continue
        for row in conn.execute(f'SELECT * FROM "{table}"'):
            values.extend(v for v in row if isinstance(v, str))
    return values


def _scan(db: Path | None, texts, kept) -> dict:
    if db is None:
        return {"status": "not_applicable", "note": "in-memory store: no file to scan"}
    files = [p for p in (db, *(Path(f"{db}{s}") for s in SIDECARS)) if p.exists()]
    result = scan_paths(files, texts, kept)
    if result["unreadable"] or result["not_scanned"] or len(result["files"]) < len(files):
        result["status"] = "incomplete"
    elif result["files_with_hits"]:
        result["status"] = "hits"
    elif result["texts_scanned"] == 0:
        result["status"] = "structural_only"
    else:
        result["status"] = "clean"
    return result


def remove_snapshots(conn: sqlite3.Connection, db: Path | None) -> dict:
    try:
        done = snapshot_dir.remove_store_snapshots(snapshot_dir.store_id_of(conn), db)
    except OSError as exc:
        return {"removed": 0, "failed": 1, "removed_while_live": 0,
                "error": exc.__class__.__name__}
    return {"removed": done["removed"], "failed": len(done["failed"]),
            "removed_while_live": done["removed_while_live"]}


def _merge_counts(first: dict | None, second: dict) -> dict:
    if not first:
        return second
    return {k: first.get(k, 0) + second.get(k, 0)
            for k in ("removed", "failed", "removed_while_live")}


def _residue(conn, work: dict, scan: dict, file_backed: bool) -> dict:
    plan = work["plan"]
    residue = {"legacy_audit_rows": work["legacy"],
               **erase_residue.kept_rows(conn, work["subject_texts"], plan["kept_sources"]),
               "audit_reasons_with_erased_text": erase_residue.audit_reasons(
                   conn, work["subject_texts"], work["last_ord_before"]),
               "unresolved_merged_sources": erase_residue.unresolved_merges(
                   conn, plan["memories"], plan["merged_away"]),
               "short_texts": {"count": scan.get("short_texts", 0),
                               "note": "texts under 16 bytes get structural checks only"},
               "kept_overlap": {"texts": scan.get("kept_overlap", 0),
                                "note": "erased texts that share a 16-byte run with "
                                        "rows the store keeps"}}
    if file_backed:
        residue["freed_clusters"] = {
            "note": "SQLite rewrote and shrank the database file, and mneme deleted "
                    "replay snapshots with a plain unlink; the disk blocks released by "
                    "both, and the deleted rollback journal, are not overwritten and "
                    "can hold erased bytes until the file system reuses them"}
    return residue


def _findings(structural, scrub, snapshots, scan, residue, outside) -> list[str]:
    held, unchecked = erase_outside.copies_found(outside)
    checks = [
        (not structural["rows_absent"], "rows_present"),
        ("remedy" in scrub, "scrub_incomplete"),
        (snapshots["failed"], "snapshot_removal_failed"),
        (scan["status"] == "hits", "scan_hits"),
        (residue["kept_rows_with_erased_text"]["rows"], "kept_rows_repeat_erased_text"),
        (residue["audit_reasons_with_erased_text"]["count"], "audit_reasons_quote_erased_text"),
        (residue["unresolved_merged_sources"]["count"], "unresolved_merged_sources"),
        (held, "copies_hold_erased_text"),
        (snapshots["removed_while_live"], "snapshot_removed_while_live"),
        (scan["status"] == "incomplete", "scan_incomplete"),
        (unchecked, "copies_unchecked"),
        (residue["kept_sources"]["rows"], "sources_kept"),
    ]
    return [name for failed, name in checks if failed]


_STATUS_OF = {"rows_present": 0, "scrub_incomplete": 0, "snapshot_removal_failed": 0,
              "scan_hits": 0, "kept_rows_repeat_erased_text": 0,
              "audit_reasons_quote_erased_text": 0, "unresolved_merged_sources": 0,
              "copies_hold_erased_text": 1, "snapshot_removed_while_live": 1,
              "scan_incomplete": 2, "copies_unchecked": 2, "sources_kept": 3}


def status_of(findings: list[str]) -> str:
    if not findings:
        return "erased"
    return ORDER[min(_STATUS_OF[f] for f in findings)]


def build_receipt(store, work: dict, scrub: dict, *, reason: str,
                  emit_openings: bool) -> dict:
    conn, plan = store.conn, work["plan"]
    db = db_path(conn)
    snapshots = _merge_counts(work.get("snapshots_before"), remove_snapshots(conn, db))
    kept = kept_values(conn)
    scan = _scan(db, work["texts"], kept)
    structural = {"rows_absent": rows_absent(conn, plan),
                  "salts_deleted": work["salts_deleted"],
                  **{k: scrub[k] for k in ("freelist_count", "journal", "wal_bytes")}}
    residue = _residue(conn, work, scan, db is not None)
    outside = erase_outside.out_of_reach(snapshot_dir.store_id_of(conn), db,
                                         work["texts"], kept)
    findings = _findings(structural, scrub, snapshots, scan, residue, outside)
    receipt = {
        "schema": RECEIPT_SCHEMA, "status": status_of(findings), "findings": findings,
        "plan_sha256": plan["plan_sha256"], "reason": reason,
        "keep_sources": plan["keep_sources"], "counts": plan["counts"],
        "erase_refs": [{"ref": o["erase_ref"], "layer": o["layer"]}
                       for o in work["openings"]],
        "scrub": {k: v for k, v in scrub.items() if k not in structural},
        "structural": structural, "scan": scan, "snapshots": snapshots,
        "residue": residue, "out_of_reach": outside,
    }
    if emit_openings:
        receipt["openings"] = work["openings"]
    return receipt


def rows_absent(conn: sqlite3.Connection, plan: dict) -> bool:
    for table, column, ids in (("memories", "id", plan["memories"]),
                               ("turns", "id", plan["turns"]),
                               ("salts", "subject_id", plan["memories"]),
                               ("merges", "kept_id", plan["memories"])):
        for item in ids:
            if conn.execute(f"SELECT 1 FROM {table} WHERE {column}=?",
                            (item,)).fetchone():
                return False
    return True
