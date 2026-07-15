"""ingest.py — the ecosystem composition: research intake -> accountable memory,
with an unbroken provenance chain from the web source to the recalled memory.

No memory product can tell you where a recalled fact ULTIMATELY came from. When
mneme ingests items from an accountable intake tool (gather, the sibling
flagship), it binds each item's origin receipt (source, ref/url, method, content
sha256) to the memory it becomes. The chain is then re-checkable end to end:

    web url --(gather sha256)--> mneme turn --> mneme atom --> recall receipt

An agent that remembers what it researched, and can prove the recalled memory
traces back to the exact bytes fetched from the exact source. That is the moat a
single-purpose memory library cannot have.

Zero-dep and decoupled: gather items arrive as plain dicts (mneme never imports
gather), so any intake tool that emits {id, text, source, ref, method, sha256}
composes here. A malformed item is skipped with its reason, never guessed.
"""
from __future__ import annotations

import re

from .extract import extract_atoms
from .receipt import content_hash

_L1_CRITERION = "atomic user fact"
_REQUIRED = ("text",)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _origin_receipt_present(origin: dict | None) -> bool:
    """A well-formed origin receipt: a fetchable ref AND a content hash that is
    at least shaped like a sha256. This is presence-of-a-checkable-receipt, not
    proof — the recheck (re-fetch, re-hash, compare) is what fully verifies."""
    return bool(origin) and bool(origin.get("ref")) and bool(
        _SHA256.match(origin.get("sha256", "")))


def _origin(item: dict) -> dict:
    """Normalize the origin receipt carried by an intake item (gather's
    Provenance shape). Only receipt fields are kept; content is not duplicated."""
    return {"source": str(item.get("source", "")),
            "ref": str(item.get("ref", item.get("url", ""))),
            "method": str(item.get("method", "")),
            "sha256": str(item.get("sha256", item.get("content_sha256", "")))}


def from_gather(memory, items: list[dict], session: str,
                *, role: str = "source") -> dict:
    """Ingest intake items into memory, binding each item's origin receipt to the
    turn it becomes, then extracting atoms. Returns a summary with per-atom
    provenance that chains back to the source. Idempotent by content id."""
    turn_rows = []
    skipped = []
    for i, item in enumerate(items):
        if any(k not in item or not str(item.get(k, "")).strip() for k in _REQUIRED):
            skipped.append({"index": i, "reason": "item has no text"})
            continue
        text = str(item["text"])
        origin = _origin(item)
        tid = str(item.get("id") or content_hash(session, str(i), origin["sha256"], text)[:16])
        memory.store.add_turn(tid, session, role, text, origin=origin)
        # the turn is stored honestly as role="source" (its origin receipt names
        # the web source); for extraction it is treated as extractable content,
        # since gathered research IS facts to remember, not conversational noise
        turn_rows.append({"id": tid, "role": "user", "text": text})
    atoms = extract_atoms(turn_rows, memory.extractor)
    receipts = []
    for aid, atom in atoms:
        memory.store.add_memory(aid, "L1", atom.text, [atom.source_id],
                                memory.extractor.name, _L1_CRITERION, session=session)
        receipts.append({"memory_id": aid, "source_turn": atom.source_id})
    return {"session": session, "ingested": len(turn_rows), "skipped": skipped,
            "atoms": len(receipts), "provenance": receipts,
            "note": "each atom's source turn carries its gather origin receipt; "
                    "walk it with provenance_chain(memory_id)"}


def provenance_chain(memory, memory_id: str) -> dict | None:
    """Walk the full chain for a memory: atom -> source turn(s) -> external origin
    receipt (the web source and the content hash fetched). Returns None if the
    memory is absent. The chain is re-checkable: re-fetch the ref, re-hash, and
    confirm it still equals the origin sha256."""
    prov = memory.store.provenance(memory_id)
    if prov is None:
        return None
    links = []
    for sid in prov.source_ids:
        turn = memory.store.turn(sid)
        origin = memory.store.turn_origin(sid)
        links.append({
            "turn_id": sid,
            "turn_present": turn is not None,
            "turn_text": turn["text"] if turn else None,
            "origin": origin,      # {source, ref, method, sha256} or None (native turn)
        })
    # fail closed: a memory with no source links is NOT externally grounded (all()
    # over an empty list is vacuously true), and a self-declared origin hash that
    # is not even sha256-shaped is not a grounding — it is an unverifiable string.
    grounded = bool(links) and all(_origin_receipt_present(l["origin"]) for l in links)
    return {"schema": "mneme.provenance-chain/1",
            "memory_id": memory_id, "text": memory.store.memory(memory_id)["text"],
            "criterion": prov.criterion, "extractor": prov.extractor,
            "chain": links,
            "externally_grounded": grounded,
            "recheck": ("re-fetch each origin.ref, re-hash the content, confirm it "
                        "equals origin.sha256 -> the memory provably traces to the source")}
