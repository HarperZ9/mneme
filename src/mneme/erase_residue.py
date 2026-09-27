"""erase_residue.py: what an erase leaves inside the store, by class.

- `kept_rows_with_erased_text`: turns and memories the store keeps that still
  repeat an erased text whole (erase_text.repeats_whole). The plan offers the
  same user's copies as duplicates; what is left here belongs to another user
  or was kept by an older plan.
- `kept_rows_with_short_text`: kept rows that hold an erased text of 8 to 15
  characters as a word inside longer text (erase_text.contains_short).
- `kept_rows_sharing_erased_run`: kept rows that share a 16-character run with
  a longer erased text without repeating it whole, such as a key quoted in
  another turn.
- `kept_sources`: the source turns the caller chose to keep. They are left out
  of the three counts above.
- `audit_reasons_with_erased_text`: audit entries, this erase's own included,
  whose free-text reason quotes an erased text or holds one of its
  digit-bearing tokens. The log is append-only, so they stay.
- `unresolved_merged_sources`: tombstones an older consolidation wrote
  ("merged into <id>") for a memory this erase removed, with no merge link.
  The merged-away duplicate's source turn cannot be found, so it may remain.
- `legacy_audit_rows`: earlier entries that name an erased row by its
  content-derived id, counted as blinded or holding unsalted hashes.
"""
from __future__ import annotations

import re
import sqlite3

from . import audit_blind, schema_guard
from .erase_text import (RUN, contains_short, norm, repeats_in, repeats_whole,
                         shares_token, windows)

UNSALTED_OPS = ("forget", "update", "supersede")
_MERGED = re.compile(r"merged into (\S+)")


def _classify(row: str, needles, long_windows: set[str]) -> str | None:
    if any(repeats_whole(n, row) for n in needles):
        return "whole"
    if any(contains_short(n, row) for n in needles):
        return "short"
    if long_windows and any(w in long_windows for w in windows(row)):
        return "run"
    return None


def kept_rows(conn: sqlite3.Connection, texts, kept_sources) -> dict:
    needles = [n for n in (norm(t) for t in texts) if n]
    long_windows: set[str] = set()
    for needle in needles:
        if len(needle) >= RUN:
            long_windows |= windows(needle)
    counts = {"whole": 0, "short": 0, "run": 0, "kept": 0}
    for table in ("turns", "memories"):
        for row_id, text in conn.execute(f"SELECT id, text FROM {table}"):
            found = _classify(norm(text), needles, long_windows)
            if found is None:
                continue
            if table == "turns" and row_id in kept_sources:
                counts["kept"] += found == "whole"      # kept by request
            else:
                counts[found] += 1
    return _kept_report(counts)


def _kept_report(counts: dict) -> dict:
    return {"kept_rows_with_erased_text": {
                "rows": counts["whole"],
                "note": "rows the store keeps that still repeat an erased text whole, "
                        "such as another user's copy; erase them to remove the text"},
            "kept_rows_with_short_text": {
                "rows": counts["short"],
                "note": "rows the store keeps that hold an erased text of 8 to 15 "
                        "characters inside longer text"},
            "kept_rows_sharing_erased_run": {
                "rows": counts["run"],
                "note": "rows the store keeps that share a 16-character run with an "
                        "erased text, such as a key quoted in another turn"},
            "kept_sources": {
                "rows": counts["kept"],
                "note": "source turns kept at the caller's request; they still hold "
                        "the text of the memories erased from them"}}


def audit_reasons(conn: sqlite3.Connection, texts) -> dict:
    needles = [n for n in (norm(t) for t in texts) if n]
    count = 0
    for (reason,) in conn.execute("SELECT reason FROM audit"):
        said = norm(reason)
        said_windows = windows(said)
        if any(repeats_in(said, said_windows, n)
               or shares_token(said, n, digits_only=True) for n in needles):
            count += 1
    return {"count": count,
            "note": "audit entries whose reason quotes erased text or holds one of "
                    "its tokens with a digit; the log is append-only and "
                    "hash-chained, so they stay"}


def unresolved_merges(conn: sqlite3.Connection, memory_ids, merged_away) -> dict:
    erased, linked, count = set(memory_ids), set(merged_away), 0
    for memory_id, reason in conn.execute(
            "SELECT memory_id, reason FROM audit WHERE op='forget'"):
        match = _MERGED.match(reason)
        if match and match.group(1) in erased and memory_id not in linked:
            count += 1
    return {"count": count,
            "note": "near-duplicates an older consolidation merged into an erased "
                    "memory without a merge link; their source turns may remain"}


def legacy_audit_rows(conn: sqlite3.Connection, subjects, merged_away=()) -> dict:
    """Earlier audit rows that still name an erased row by content-derived value.

    `blinded` counts the rows whose hashes are schema 5 commitments; this erase
    deletes their salts. `unsalted_hashes` counts rows from before schema 5 (or
    written by an older mneme since) that keep a plain hash of a version."""
    ids = {s.id for s in subjects} | set(merged_away)
    hashes = {s.content_sha256 for s in subjects if s.content_sha256}
    long_ids = [i for i in ids if len(i) >= 12]
    count = unsalted = blinded = 0
    for op, memory_id, before, after, reason in conn.execute(
            "SELECT op, memory_id, before_sha, after_sha, reason FROM audit"):
        if (memory_id in ids or before in hashes or after in hashes
                or any(i in reason for i in long_ids)):
            count += 1
            values = [v for v in (before, after) if v]
            if op in UNSALTED_OPS and values:
                if all(audit_blind.is_blinded(v) for v in values):
                    blinded += 1
                else:
                    unsalted += 1
    return {"count": count, "unsalted_hashes": unsalted, "blinded": blinded,
            "note": "audit rows written before this erase keep the content-derived "
                    "ids of the rows they describe, and such an id confirms a guessed "
                    "text when its source turn id is known; the log is append-only, so "
                    "they stay. Rows from before schema 5 also keep unsalted hashes. "
                    "Blinded values lose their salts in this erase and open to nothing"}


def schema_downgrade(conn: sqlite3.Connection) -> dict:
    """Whether an older mneme reopened this store, as schema_guard recorded it."""
    try:
        stamped = schema_guard.read(conn)["downgrade_seen"]
    except sqlite3.Error:
        stamped = None
    return {"stamped": stamped,
            "note": "an older mneme reopened this database and stamped this schema "
                    "version; rows it wrote carry that version's guarantees, such as "
                    "unsalted hashes, which the legacy audit rows count"
                    if stamped is not None else "no older mneme has reopened this "
                                                "database since 0.5.0 opened it"}
