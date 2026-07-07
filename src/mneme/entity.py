"""entity.py — a grounded entity graph over memories (mneme's graph memory).

Mem0's graph-memory mode extracts entities and relationships. mneme's
deterministic floor extracts the shape personal memory actually is — typed
triples about the subject — and grounds every edge in the atom it came from, so
the graph is drift-checkable: forget or drift an atom and the edges it supported
go with it. No black-box graph; every relationship cites its evidence.

Extracted, deterministically and zero-dep:
  - TYPED RELATIONS: (subject, predicate, object) where the predicate is a value
    cue (lives_in, works_in, prefers, allergic_to, uses, likes) and the object is
    the salient phrase after it. Subject defaults to the user (personal memory).
  - NAMED ENTITIES: capitalized proper nouns (mid-sentence), as nodes.
  - Each edge carries `source` = the atom id -> the graph is exactly as fresh as
    its atoms.

HONEST SCOPE: this is cue-based structured extraction, not general NER. It will
not resolve "the Rockies" to a mountain range or link coreferent mentions; a
real NER/LLM extractor plugs in as an edge for that. What it earns is a
drift-checkable relationship graph with zero dependencies.
"""
from __future__ import annotations

import re

_PREDICATES = {
    "lives_in": r"\b(?:live|lives|living|based|located)\s+(?:in|at|near)\s+",
    "works_in": r"\b(?:work|works|working)\s+(?:in|at|as|on)\s+",
    "prefers": r"\b(?:prefer|prefers|favou?rite)\s+",
    "allergic_to": r"\ballergic\s+to\s+",
    "uses": r"\b(?:use|uses|using)\s+",
    "likes": r"\b(?:like|likes|love|loves|enjoy|enjoys)\s+",
}
_PROPER = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*)\b")
_TAIL = re.compile(r"[a-z0-9][a-z0-9\s'-]*", re.IGNORECASE)
_STOP_OBJ = {"the", "a", "an", "in", "at", "to", "and", "or", "over", "on"}


def _clean_object(phrase: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9'-]+", phrase.lower())]
    while words and words[0] in _STOP_OBJ:
        words.pop(0)
    # keep up to a short noun phrase, stop at a conjunction
    out = []
    for w in words[:5]:
        if w in ("and", "but", "because", "so"):
            break
        out.append(w)
    return " ".join(out).strip()


def relations_in(text: str, *, subject: str = "user") -> list[dict]:
    """Typed (subject, predicate, object) triples from one atom's text."""
    out = []
    low = text.lower()
    for pred, cue in _PREDICATES.items():
        for m in re.finditer(cue, low):
            tail = _TAIL.match(low, m.end())
            obj = _clean_object(tail.group(0)) if tail else ""
            if obj:
                out.append({"subject": subject, "predicate": pred, "object": obj})
    return out


def named_entities(text: str) -> list[str]:
    """Capitalized proper nouns mid-sentence (skip the sentence-initial word)."""
    ents = []
    for sent in re.split(r"(?<=[.!?])\s+", text):
        toks = sent.split()
        for i, _ in enumerate(toks):
            for m in _PROPER.finditer(" ".join(toks[1:])):   # skip first token
                cand = m.group(1)
                if cand not in ents:
                    ents.append(cand)
            break
    return ents


def entity_graph(memory, *, user: str | None = None, session: str | None = None) -> dict:
    """Build the grounded entity graph over the (optionally user/session-scoped)
    L1 atoms. Each edge cites the atom it came from -> drift-checkable. Nodes are
    the subject, the relation objects, and named entities."""
    atoms = memory.store.memories(layer="L1", user=user, session=session)
    subject = user or "user"
    nodes: dict[str, dict] = {subject: {"id": subject, "kind": "subject"}}
    edges: list[dict] = []
    for a in atoms:
        for rel in relations_in(a["text"], subject=subject):
            obj = rel["object"]
            nodes.setdefault(obj, {"id": obj, "kind": "value"})
            edges.append({"from": rel["subject"], "predicate": rel["predicate"],
                          "to": obj, "source": a["id"]})
        for ent in named_entities(a["text"]):
            key = f"~{ent}"
            nodes.setdefault(key, {"id": ent, "kind": "named_entity"})
            edges.append({"from": subject, "predicate": "mentions",
                          "to": key, "source": a["id"]})
    # dedup edges deterministically
    seen = set()
    uniq = []
    for e in sorted(edges, key=lambda e: (e["from"], e["predicate"], e["to"], e["source"])):
        k = (e["from"], e["predicate"], e["to"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return {
        "schema": "mneme.entity-graph/1",
        "scope": {"user": user, "session": session},
        "nodes": sorted(nodes.values(), key=lambda n: (n["kind"], n["id"])),
        "edges": uniq,
        "grounded": all(memory.store.memory(e["source"]) is not None for e in uniq),
        "note": ("every edge cites the atom it came from — forget/drift that atom "
                 "and the edge is stale; the graph is exactly as fresh as its memories"),
    }
