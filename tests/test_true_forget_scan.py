"""Falsifiers for the erase scrub and its residual scan.

The long canary is more than 10 KiB of random text with letters whose NFC and
NFD forms differ, so it spans SQLite overflow pages, where a 4-byte page
pointer splits the stored bytes. The test searches the database files for
every 16-byte window of it with its own code, so a scanner that under-reports
cannot make this test pass. The scanner itself gets controls it must flag.
"""
from __future__ import annotations

import random
import sqlite3
import unicodedata
from pathlib import Path

from mneme import AgentMemory
from mneme.erase_scan import scan_paths

W = 16
LETTERS = "abcdefghijklmnopqrstuvwxyz" + "éñø" + "éñ"


def _canary(seed: int, size: int = 10_500) -> str:
    rng = random.Random(seed)
    words = ["".join(rng.choice(LETTERS) for _ in range(rng.randint(3, 9)))
             for _ in range(size // 5)]
    text = "I remember " + " ".join(words)
    return text[:size] + "."


def _windows(data: bytes, step: int = 1) -> set[bytes]:
    return {data[i:i + W] for i in range(0, len(data) - W + 1, step)}


def _files(db: Path) -> list[Path]:
    paths = [db, *(Path(f"{db}{s}") for s in ("-journal", "-wal", "-shm"))]
    return [p for p in paths if p.exists()]


def _residual_windows(db: Path, text: str) -> int:
    """Count 16-byte windows of `text` (every form) found in the store's files."""
    wanted: set[bytes] = set()
    for form in {text, unicodedata.normalize("NFC", text),
                 unicodedata.normalize("NFD", text)}:
        wanted |= _windows(form.encode("utf-8"))
        wanted |= _windows(form.encode("utf-16-le"), step=2)
    found = 0
    for path in _files(db):
        found += len(wanted & _windows(path.read_bytes()))
    return found


def _memory_with_canary(db: Path, canary: str, *, wal: bool = False):
    memory = AgentMemory(db)
    if wal:
        assert memory.store.conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    memory.remember("s", [{"id": "canary", "role": "user", "text": canary},
                          {"id": "keep", "role": "user", "text": "I prefer green tea daily."}])
    return memory


def test_a_long_canary_leaves_no_16_byte_window_in_any_store_file(tmp_path):
    db = tmp_path / "mneme.db"
    canary = _canary(1)
    memory = _memory_with_canary(db, canary)
    atom = next(r["id"] for r in memory.store.memories(layer="L1")
                if r["text"].startswith("I remember"))
    assert _residual_windows(db, canary) > 0              # the control: it was there

    receipt = memory.forget(atom, reason="user asked")
    memory.close()

    assert receipt["status"] == "erased"
    assert receipt["scan"]["status"] == "clean"
    assert _residual_windows(db, canary) == 0
    assert "I prefer green tea daily." in db.read_bytes().decode("utf-8", "replace")


def test_a_wal_database_is_checkpointed_and_scanned_clean(tmp_path):
    db = tmp_path / "mneme.db"
    canary = _canary(2)
    memory = _memory_with_canary(db, canary, wal=True)
    atom = next(r["id"] for r in memory.store.memories(layer="L1")
                if r["text"].startswith("I remember"))

    receipt = memory.forget(atom, reason="user asked")

    assert receipt["scan"]["status"] == "clean"
    assert receipt["structural"]["wal_bytes"] == 0
    assert _residual_windows(db, canary) == 0
    memory.close()


def test_the_scrub_turns_on_secure_delete_and_keeps_vacuum_out_of_temp(tmp_path):
    db = tmp_path / "mneme.db"
    memory = _memory_with_canary(db, _canary(3))
    atom = next(r["id"] for r in memory.store.memories(layer="L1")
                if r["text"].startswith("I remember"))
    executed: list[str] = []
    memory.store.conn.set_trace_callback(executed.append)

    memory.forget(atom, reason="user asked")

    statements = [s.strip().upper() for s in executed]
    first_delete = next(i for i, s in enumerate(statements) if s.startswith("DELETE"))
    vacuum = statements.index("VACUUM")
    assert "PRAGMA SECURE_DELETE=ON" in statements[:first_delete]
    assert "PRAGMA TEMP_STORE=MEMORY" in statements[:vacuum]
    memory.close()


def test_the_scanner_flags_text_split_by_page_pointers_and_other_encodings(tmp_path):
    canary = _canary(4).encode("utf-8")
    split = b"".join(canary[i:i + 4000] + b"\x00\x00\x0f\xa1"
                     for i in range(0, len(canary), 4000))
    cases = {"split.db": split, "utf16.db": _canary(4).encode("utf-16-le"),
             "clean.db": b"SQLite format 3\x00" + b"\x00" * 8000}
    for name, data in cases.items():
        (tmp_path / name).write_bytes(data)

    result = scan_paths([tmp_path / n for n in cases], [_canary(4)], kept=[])

    assert result["files_with_hits"] == ["split.db", "utf16.db"]
    assert result["texts_with_hits"] == 1


def test_text_that_survives_inside_a_kept_row_is_overlap_not_residue(tmp_path):
    kept_row = "I live in Denver with my dog."
    path = tmp_path / "store.db"
    path.write_bytes(b"\x00" * 64 + kept_row.encode("utf-8") + b"\x00" * 64)

    counted = scan_paths([path], ["I live in Denver."], kept=[kept_row])
    alone = scan_paths([path], ["I live in Denver!"], kept=[])

    assert counted["files_with_hits"] == [] and counted["kept_overlap"] == 1
    assert alone["files_with_hits"] == ["store.db"]


def test_short_texts_get_structural_checks_only_and_are_counted(tmp_path):
    path = tmp_path / "store.db"
    path.write_bytes(b"I am Bo." + b"\x00" * 32)

    result = scan_paths([path], ["I am Bo."], kept=[])

    assert result["short_texts"] == 1 and result["texts_scanned"] == 0
    assert result["files_with_hits"] == []


def test_an_in_memory_store_reports_that_no_file_was_scanned():
    memory = AgentMemory(":memory:")
    memory.remember("s", [{"id": "t1", "role": "user", "text": "I live in Denver today."}])
    atom = memory.store.memories(layer="L1")[0]["id"]

    receipt = memory.forget(atom, reason="user asked")

    assert receipt["scan"]["status"] == "not_applicable"
    assert receipt["status"] == "erased"


def test_a_reader_that_pins_the_wal_is_reported_and_a_later_scrub_finishes(
        tmp_path, monkeypatch):
    import mneme.erase as erase_module
    from mneme.erase import scrub_store

    monkeypatch.setattr(erase_module, "CHECKPOINT_RETRY_SECONDS", 0.0)
    db = tmp_path / "mneme.db"
    canary = _canary(5)
    _memory_with_canary(db, canary, wal=True).close()
    reader = sqlite3.connect(db)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM turns").fetchone()   # pins the old pages
    memory = AgentMemory(db)
    atom = next(r["id"] for r in memory.store.memories(layer="L1")
                if r["text"].startswith("I remember"))

    receipt = memory.forget(atom, reason="user asked")

    assert receipt["scrub"]["checkpoint"] == "busy"
    assert receipt["status"] == "erased_residue_found"
    assert receipt["scan"]["status"] == "hits"
    assert "mneme scrub" in receipt["scrub"]["remedy"]
    reader.rollback()
    reader.close()
    finished = scrub_store(memory.store)
    assert finished["checkpoint"] == "complete" and finished["wal_bytes"] == 0
    memory.close()
    assert _residual_windows(db, canary) == 0
