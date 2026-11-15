"""forensic_util.py: an independent raw-byte search for a marker, for the claim tests.

This file shares no code with mneme's own residual scan (erase_scan.py), so a
scan that under-reports cannot make these tests pass. `forms` lists the byte
forms a stored marker commonly takes: UTF-8, UTF-16 and UTF-32 in both byte
orders, base64 in its three alignments (standard and URL-safe), hex in both
cases, percent-encoding, HTML and JSON escapes, reversed text and case folds.
`hits` reads every file under the given roots and reports each form found,
whole or as any 12-byte window of it, so a marker that SQLite split across an
overflow page is still found. It also checks file and directory names.
"""
from __future__ import annotations

import base64
import html
import json
import secrets
import urllib.parse
from pathlib import Path

WINDOW = 12


def new_marker(label: str = "canary") -> str:
    """A high-entropy marker: 192 random bits in mixed-case base32 and hex."""
    raw = secrets.token_bytes(24)
    return f"Zq{label}{base64.b32encode(raw).decode('ascii').rstrip('=')}x{raw.hex()}Qz"


def _base64_forms(data: bytes) -> dict[str, bytes]:
    out = {}
    for pad in range(3):
        for name, enc in (("b64", base64.b64encode), ("b64url", base64.urlsafe_b64encode)):
            body = enc(b"\x00" * pad + data).rstrip(b"=")
            # the first and last groups depend on the neighbours: drop them
            start = 4 if pad else 0
            out[f"{name}+{pad}"] = body[start:len(body) - 4]
    return out


def forms(marker: str) -> dict[str, bytes]:
    """Every byte form of `marker` the search looks for, by name."""
    utf8 = marker.encode("utf-8")
    out = {
        "utf-8": utf8,
        "utf-16-le": marker.encode("utf-16-le"),
        "utf-16-be": marker.encode("utf-16-be"),
        "utf-32-le": marker.encode("utf-32-le"),
        "utf-32-be": marker.encode("utf-32-be"),
        "hex-lower": utf8.hex().encode("ascii"),
        "hex-upper": utf8.hex().upper().encode("ascii"),
        "percent": urllib.parse.quote(marker, safe="").encode("ascii"),
        "percent-all": "".join(f"%{b:02X}" for b in utf8).encode("ascii"),
        "html": html.escape(marker).encode("utf-8"),
        "html-numeric": "".join(f"&#{ord(c)};" for c in marker).encode("ascii"),
        "json": json.dumps(marker)[1:-1].encode("ascii"),
        "json-u": "".join(f"\\u{ord(c):04x}" for c in marker).encode("ascii"),
        "reversed": utf8[::-1],
        "lower": marker.lower().encode("utf-8"),
        "upper": marker.upper().encode("utf-8"),
    }
    out.update(_base64_forms(utf8))
    return out


def _windows(data: bytes) -> list[bytes]:
    if len(data) <= WINDOW:
        return [data]
    return [data[i:i + WINDOW] for i in range(len(data) - WINDOW + 1)]


def files_under(*roots: Path) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        root = Path(root)
        if root.is_file():
            found.append(root)
        elif root.is_dir():
            found.extend(p for p in sorted(root.rglob("*")) if p.is_file())
    return found


def hits(marker: str, *roots: Path) -> list[str]:
    """`<file>: <form>` for every form of `marker` found under `roots`.

    Raises when a file cannot be read: an unread file is not a clean file."""
    wanted = {name: _windows(data) for name, data in forms(marker).items()}
    found = []
    for path in files_under(*roots):
        data = path.read_bytes()
        name_bytes = str(path).encode("utf-8", "surrogatepass")
        for form, windows in wanted.items():
            if any(w in data for w in windows):
                found.append(f"{path.name}: {form}")
            if any(w in name_bytes for w in windows):
                found.append(f"{path.name}: {form} in the file name")
    return found


def digest_hits(values, *roots: Path) -> list[str]:
    """`<file>: <value>` for each of `values` (ids, digests) found under `roots`."""
    found = []
    for path in files_under(*roots):
        data = path.read_bytes()
        for value in values:
            if value and value.encode("ascii") in data:
                found.append(f"{path.name}: {value}")
    return found
