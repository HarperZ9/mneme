"""Falsifiers for the forget copy: no legal framing of what forget does.

"GDPR erasure" and "right to be forgotten" name a legal outcome. Erasure under
a data-protection law reaches backups, exports and every processor, and a
local memory store reaches none of those, so the copy describes what forget
removes and what it leaves instead. The check normalizes whitespace, because a
phrase can wrap across a line break. A shipped record keeps its wording when a
dated correction line follows it within ten lines.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHRASES = (r"\bG\s*D\s*P\s*R\b", r"\bright[\s-]+to[\s-]+be[\s-]+forgotten\b")
CORRECTION = re.compile(r"^\s*(?:[-*>]\s*)?Correction, \d{4}-\d{2}-\d{2}:")
SUFFIXES = {".md", ".py", ".toml", ".json", ".txt", ".yml", ".yaml", ".cfg"}
SKIP_DIRS = {".git", "__pycache__", "build", "dist", ".venv", "venv",
             ".pytest_cache", "node_modules"}
THIS_FILE = Path(__file__).resolve()


def _files(root: Path):
    for path in sorted(root.rglob("*")):
        parts = set(path.relative_to(root).parts)
        if (path.is_file() and path.suffix in SUFFIXES and not parts & SKIP_DIRS
                and not any(p.endswith(".egg-info") for p in parts)
                and path.resolve() != THIS_FILE):
            yield path


def _corrected(lines: list[str], line_no: int) -> bool:
    return any(CORRECTION.match(line) for line in lines[line_no:line_no + 10])


def findings(root: Path) -> list[str]:
    found = []
    for path in _files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for pattern in PHRASES:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                line_no = text.count("\n", 0, match.start()) + 1
                if not _corrected(lines, line_no):
                    found.append(f"{path.relative_to(root)}:{line_no}")
    return found


def test_no_file_carries_legal_framing_of_forget():
    assert findings(ROOT) == []


def test_the_check_flags_planted_phrases_and_accepts_a_dated_correction(tmp_path):
    (tmp_path / "plain.md").write_text("forget gives GDPR-style deletion\n", encoding="utf-8")
    (tmp_path / "wrapped.py").write_text('"""a right to be\n   forgotten flow"""\n',
                                         encoding="utf-8")
    (tmp_path / "late.md").write_text("GDPR erasure\n" + "x\n" * 10 +
                                      "Correction, 2026-09-26: too late.\n", encoding="utf-8")
    (tmp_path / "fixed.md").write_text(
        "- forget (GDPR erasure) removes.\n"
        "- Correction, 2026-09-26: forget removed the memory row only.\n",
        encoding="utf-8")

    assert findings(tmp_path) == ["late.md:1", "plain.md:1", "wrapped.py:1"]


LOCALITY = re.compile(r"fully\s+local|stays\s+on\s+your\s+machine", re.IGNORECASE)
QUALIFIER = re.compile(r"provider", re.IGNORECASE)


def locality_findings(root: Path) -> list[str]:
    """A locality claim must name, in the same paragraph, what leaves the machine."""
    found = []
    for path in _files(root):
        if path.suffix != ".md":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for paragraph_start, paragraph in _paragraphs(text):
            if LOCALITY.search(paragraph) and not QUALIFIER.search(paragraph):
                line_no = text.count("\n", 0, paragraph_start) + 1
                found.append(f"{path.relative_to(root)}:{line_no}")
    return found


def _paragraphs(text: str):
    for match in re.finditer(r"\S(?:.|\n(?![ \t]*\n))*", text):
        yield match.start(), match.group(0)


def test_no_doc_claims_locality_without_naming_model_providers():
    assert locality_findings(ROOT) == []


def test_the_locality_check_flags_a_bare_claim_and_accepts_a_qualified_one(tmp_path):
    (tmp_path / "bare.md").write_text("Zero deps, fully local.\n", encoding="utf-8")
    (tmp_path / "ok.md").write_text(
        "Local by default: the store stays on your machine; text you send to an\n"
        "LLM extractor goes to that model's provider.\n", encoding="utf-8")

    assert locality_findings(tmp_path) == ["bare.md:1"]
