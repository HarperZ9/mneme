"""erase_scan.py: search store files for bytes of erased text.

A hit is a run of at least 16 bytes of an erased text, in UTF-8 or either
UTF-16 byte order, found in a file, that is not also a run of a row the store
keeps. Runs inside kept rows are explained by those rows (for example "I live
in Denver" inside a kept "I live in Denver with my dog"), and the result
counts such texts as kept overlap instead.

SQLite splits long text across overflow pages, each of which begins with a
4-byte page pointer, so a whole-string search misses stored text. The scan
indexes every 8-byte window of each erased text and intersects that index with
the file's 8-byte-aligned words. Any surviving run of 15 bytes or more
contains one aligned word, and each candidate is extended both ways and kept
only when it reaches 16 bytes. The work is linear in file size.

Texts shorter than 16 bytes cannot be told apart from ordinary bytes, so they
are counted as short texts and left to structural checks.

What this does not show: that freed disk blocks, backups, snapshots outside
the listed files, or other encodings are free of the text.
"""
from __future__ import annotations

import sys
from pathlib import Path

W = 16
_K = 8
FORMS = ("utf-8", "utf-16-le", "utf-16-be")
_BLOCK = 1 << 25                   # 32 MiB, a multiple of 8
_OVERLAP = 1 << 16                 # runs that cross a block edge stay whole
_SEP = b"\x00" * 8                 # no text run spans eight NUL bytes
_MAX_EXTEND = 1 << 12
_MAX_POSITIONS = 64


def _encode(texts: list[str]) -> list[tuple[int, str, bytes]]:
    return [(i, form, text.encode(form, "surrogatepass"))
            for i, text in enumerate(texts) for form in FORMS]


def _index(encoded) -> dict[int, list[tuple[int, int]]]:
    index: dict[int, list[tuple[int, int]]] = {}
    for slot, (_i, _form, data) in enumerate(encoded):
        for offset in range(len(data) - _K + 1):
            key = int.from_bytes(data[offset:offset + _K], sys.byteorder)
            bucket = index.setdefault(key, [])
            if len(bucket) < 16:
                bucket.append((slot, offset))
    return index


def _run(a: bytes, a0: int, b: bytes, b0: int, limit: int, step: int) -> int:
    """Length of the common run from a[a0] and b[b0] in direction `step`."""
    def same(n: int) -> bool:
        if step > 0:
            return a[a0:a0 + n] == b[b0:b0 + n]
        return a[a0 - n:a0] == b[b0 - n:b0]
    low, high, grow = 0, limit, 16
    while low < high:
        probe = min(high, low + grow)
        if same(probe):
            low, grow = probe, grow * 2
        else:
            high = probe - 1
            break
    while low < high:
        middle = (low + high + 1) // 2
        low, high = (middle, high) if same(middle) else (low, middle - 1)
    return low


def _extend(block: bytes, pos: int, data: bytes, offset: int) -> tuple[int, int]:
    left = _run(block, pos, data, offset, min(pos, offset, _MAX_EXTEND), -1)
    right = _run(block, pos + _K, data, offset + _K,
                 min(len(block) - pos - _K, len(data) - offset - _K, _MAX_EXTEND), 1)
    return offset - left, left + _K + right


def _matches(block: bytes, index, keys: set[int], encoded, skip: set[int]):
    """Yield (slot, start, length) for runs of at least W bytes in `block`."""
    usable = len(block) - len(block) % _K
    if usable == 0:
        return
    for value in keys.intersection(memoryview(block)[:usable].cast("Q")):
        probe, start = value.to_bytes(_K, sys.byteorder), 0
        for _ in range(_MAX_POSITIONS):
            pos = block.find(probe, start)
            if pos < 0:
                break
            start = pos + 1
            for slot, offset in index[value]:
                if encoded[slot][0] in skip:
                    continue
                begin, length = _extend(block, pos, encoded[slot][2], offset)
                if length >= W:
                    yield slot, begin, length


def _blocks(path: Path):
    with path.open("rb") as handle:
        position = 0
        while True:
            handle.seek(position)
            data = handle.read(_BLOCK + _OVERLAP)
            if data:
                yield data
            if len(data) < _BLOCK + _OVERLAP:
                return
            position += _BLOCK


def _residue(path: Path, index, keys, encoded, corpus: dict[str, bytes]) -> set[int]:
    found: set[int] = set()
    for block in _blocks(path):
        for slot, begin, length in _matches(block, index, keys, encoded, found):
            text_no, form, data = encoded[slot]
            if data[begin:begin + length] not in corpus[form]:
                found.add(text_no)
    return found


def _overlap(index, keys, encoded, corpus: dict[str, bytes]) -> set[int]:
    shared: set[int] = set()
    for form in FORMS:
        for slot, _b, _l in _matches(corpus[form], index, keys, encoded, shared):
            if encoded[slot][1] == form:
                shared.add(encoded[slot][0])
    return shared


def scan_paths(paths, texts, kept) -> dict:
    """Scan `paths` for runs of `texts` that no string in `kept` explains."""
    unique = list(dict.fromkeys(t for t in texts if t))
    long_texts = [t for t in unique if len(t.encode("utf-8")) >= W]
    encoded = _encode(long_texts)
    index = _index(encoded)
    keys = set(index)
    corpus = {form: _SEP.join(k.encode(form, "surrogatepass") for k in kept)
              for form in FORMS}
    per_file: dict[str, set[int]] = {}
    unreadable = []
    for path in map(Path, paths):
        try:
            per_file[path.name] = _residue(path, index, keys, encoded, corpus)
        except OSError:
            unreadable.append(path.name)
    hit_texts = set().union(*per_file.values()) if per_file else set()
    return {"files": sorted(per_file), "unreadable": unreadable,
            "files_with_hits": sorted(name for name, hit in per_file.items() if hit),
            "texts_scanned": len(long_texts), "texts_with_hits": len(hit_texts),
            "short_texts": len(unique) - len(long_texts),
            "kept_overlap": len(_overlap(index, keys, encoded, corpus))}
