"""Falsifiers for residue that hides behind many kept copies of its words.

The scanner used to look at no more than 64 places for each 8-byte word and
to index no more than 16 places per word of an erased text. Kept rows that
share a word with the residue, placed earlier in the file, then used up the
budget, and a real 20-byte residue was reported clean.
"""
from __future__ import annotations

from mneme.erase_scan import scan_paths

KEPT = "I live in Denver, kept row. "
RESIDUE = "I live in Denver!!!!"


def _file(tmp_path, decoys: int):
    body = (KEPT * decoys).encode("utf-8")
    body += b"\x00" * (-len(body) % 8 + 8)             # the residue starts aligned
    path = tmp_path / f"decoys-{decoys}.db"
    path.write_bytes(body + RESIDUE.encode("utf-8") + b"\x00" * 16)
    return path


def test_residue_after_eighty_kept_copies_of_its_words_is_found(tmp_path):
    result = scan_paths([_file(tmp_path, 80)], [RESIDUE], kept=[KEPT])

    assert result["files_with_hits"] == ["decoys-80.db"]


def test_the_control_with_few_decoys_is_found_and_kept_text_alone_is_clean(tmp_path):
    few = scan_paths([_file(tmp_path, 3)], [RESIDUE], kept=[KEPT])
    kept_only = tmp_path / "kept.db"
    kept_only.write_bytes((KEPT * 80).encode("utf-8"))

    assert few["files_with_hits"] == ["decoys-3.db"]
    assert scan_paths([kept_only], [RESIDUE], kept=[KEPT])["files_with_hits"] == []


def test_an_erased_text_with_a_repeated_word_is_indexed_everywhere(tmp_path):
    text = "abcdefgh" * 20 + " planted tail text"
    path = tmp_path / "repeat.db"
    path.write_bytes(b"\x00" * 8 + text[-40:].encode("utf-8") + b"\x00" * 8)

    assert scan_paths([path], [text], kept=[])["files_with_hits"] == ["repeat.db"]
