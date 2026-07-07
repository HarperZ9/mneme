"""extract.py — L0 turns into L1 atoms, deterministically, with provenance.

The class leader extracts atoms with an LLM. mneme's FLOOR is deterministic and
zero-dep: a rule-based extractor that pulls fact-shaped sentences (first-person
statements, preferences, identity, and declaratives) from user turns, so a
memory can be built with no model and no API — and the same turns always yield
the same atoms (a memory you can rebuild bit-for-bit). An LLM extractor is a
pluggable edge (the Extractor protocol) for richer atoms, but it never replaces
the auditable floor.

Every atom carries the turn id it came from as provenance, so drift.py can later
ask "does this atom still match its source?".
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .receipt import content_hash

# first-person / preference / identity cues that mark a sentence as a durable fact
_FACT_CUES = re.compile(
    r"\b(i|my|mine|we|our)\b|\b(prefer|like|love|hate|want|need|use|work|live|"
    r"named?|call(ed)?|am|is|are|was|born|allergic|avoid|always|never)\b",
    re.IGNORECASE)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True, slots=True)
class Atom:
    text: str
    source_id: str             # the turn it was extracted from


class Extractor(Protocol):
    name: str

    def extract(self, turn_id: str, role: str, text: str) -> list[Atom]: ...


class RuleExtractor:
    """Deterministic, zero-dep. Extracts fact-shaped sentences from USER turns
    (assistant turns are context, not the user's durable memory)."""

    name = "rule/v1"

    def __init__(self, *, min_words: int = 3, user_only: bool = True):
        self.min_words = min_words
        self.user_only = user_only

    def extract(self, turn_id: str, role: str, text: str) -> list[Atom]:
        if self.user_only and role.lower() not in ("user", "human"):
            return []
        atoms: list[Atom] = []
        seen: set[str] = set()
        for raw in _SENT_SPLIT.split(text):
            s = raw.strip()
            if len(s.split()) < self.min_words:
                continue
            if not _FACT_CUES.search(s):
                continue
            key = re.sub(r"\s+", " ", s.lower())
            if key in seen:
                continue
            seen.add(key)
            atoms.append(Atom(text=s, source_id=turn_id))
        return atoms


def atom_id(atom: Atom, criterion: str) -> str:
    """Deterministic id: content hash of (text, source, criterion), first 16 hex.
    Identical atoms from the same source collapse to one id (idempotent ingest)."""
    return content_hash(atom.text, atom.source_id, criterion)[:16]


def extract_atoms(turns: Sequence, extractor: Extractor) -> list[tuple[str, Atom]]:
    """Run `extractor` over a sequence of turn rows (id, role, text). Returns
    (atom_id, Atom) pairs, deduplicated by id, in stable order."""
    out: list[tuple[str, Atom]] = []
    seen: set[str] = set()
    for t in turns:
        tid, role, text = t["id"], t["role"], t["text"]
        for atom in extractor.extract(tid, role, text):
            aid = atom_id(atom, extractor.name)
            if aid in seen:
                continue
            seen.add(aid)
            out.append((aid, atom))
    return out
