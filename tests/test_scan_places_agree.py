"""The residual scan finds the same places by either search.

erase_scan._places has two ways to locate indexed words in a block: a search
per word, and one pass over the block's words when many distinct words are
present. The second exists for speed on stores whose rows share phrasing
with the erased text. Both must report the same residue.
"""
from __future__ import annotations

import random

import pytest

from mneme import erase_scan

WORDS = ("amber basalt cedar delta ember fjord garnet harbor indigo juniper "
         "kestrel lagoon maple nectar onyx prairie quartz raven").split()


def _sentence(rng: random.Random) -> str:
    return "I keep item named " + " ".join(rng.choice(WORDS) for _ in range(7)) + "."


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_both_searches_report_the_same_residue(tmp_path, monkeypatch, seed):
    rng = random.Random(seed)
    erased = [_sentence(rng) for _ in range(6)]
    kept = [_sentence(rng) for _ in range(300)]
    residue = tmp_path / "residue.bin"
    residue.write_bytes(b"\x00" * 13 + "\x00".join(kept[:150]).encode()
                        + b"\x00" * 5 + erased[0].encode() + b"\x01\x02"
                        + erased[1].encode("utf-16-le") + b"\x00" * 9)
    clean = tmp_path / "clean.bin"
    clean.write_bytes("\x00".join(kept).encode())
    results = []
    for limit in (0, 10**9):
        monkeypatch.setattr(erase_scan, "_SEARCH_PER_WORD", limit)
        results.append(erase_scan.scan_paths([residue, clean], erased, kept))
    assert results[0] == results[1]
    assert results[0]["files_with_hits"] == ["residue.bin"]
    assert results[0]["texts_with_hits"] == 2
