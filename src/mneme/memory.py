"""memory.py — AgentMemory: the accountable agent-memory database, composed.

One facade over the organs: remember turns (L0) -> extract atoms (L1) with
provenance -> recall with a re-derivable receipt -> flag drift when a source
changes -> synthesize a persona (L3) from the atoms. Matches the class leader's
4-tier surface and hybrid retrieval; adds the three things none of them have:
a provenance receipt per memory, a recall receipt that reproduces the ranking,
and a drift verdict that makes a stale memory say so.

Zero external dependencies (stdlib sqlite3). An embedder and an LLM extractor
are optional edges injected here; the deterministic floor works with neither.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .drift import drift_report
from .extract import Atom, Extractor, RuleExtractor, extract_atoms
from .recall import Embedder, recall
from .receipt import RecallReceipt, content_hash
from .store import Store

_L1_CRITERION = "atomic user fact"


class AgentMemory:
    def __init__(self, path: str | Path = ":memory:", *,
                 extractor: Extractor | None = None,
                 embedder: Embedder | None = None,
                 embed=None):
        from .embed import resolve_embedder
        self.store = Store(path)
        self.extractor = extractor or RuleExtractor()
        # `embed="ngram"` turns on the zero-dep local vector channel; `embedder=`
        # takes a real embedding model. embedder wins if both are given.
        self.embedder = embedder or resolve_embedder(embed)

    # -- ingest --------------------------------------------------------------
    def remember(self, session: str, turns: Sequence[dict]) -> dict:
        """Record raw turns (L0) and extract atoms (L1) with provenance. Each
        turn is {role, text} (+ optional id). Idempotent: identical turns and
        atoms collapse by content id, so re-ingesting the same session is a
        no-op. Returns a summary with the provenance receipts."""
        turn_rows = []
        for i, t in enumerate(turns):
            tid = t.get("id") or content_hash(session, str(i), t["role"], t["text"])[:16]
            self.store.add_turn(tid, session, t["role"], t["text"])
            turn_rows.append({"id": tid, "role": t["role"], "text": t["text"]})
        atoms = extract_atoms(turn_rows, self.extractor)
        receipts = []
        for aid, atom in atoms:
            r = self.store.add_memory(aid, "L1", atom.text, [atom.source_id],
                                      self.extractor.name, _L1_CRITERION, session=session)
            receipts.append(r.as_dict())
        return {"session": session, "turns": len(turn_rows), "atoms": len(receipts),
                "extractor": self.extractor.name, "provenance": receipts}

    # -- recall --------------------------------------------------------------
    def recall(self, query: str, *, strategy: str = "hybrid", top_k: int = 5,
               layer: str | None = None, recency_weight: float = 0.0) -> RecallReceipt:
        """Retrieve memories for `query` with a re-derivable ranking receipt.
        `layer` None searches L1 atoms (the durable facts). recency_weight > 0
        prefers recent memories (transparently — the component is in the receipt)."""
        rows = [{"id": r["id"], "text": r["text"], "layer": r["layer"], "ord": r["created_ord"]}
                for r in self.store.memories(layer=layer or "L1")]
        return recall(query, rows, strategy=strategy, top_k=top_k,
                      embedder=self.embedder, recency_weight=recency_weight)

    # -- accountability ------------------------------------------------------
    def drift(self, layer: str | None = "L1") -> dict:
        """Verdict every memory's grounding against the current store."""
        return drift_report(self.store, layer=layer)

    def provenance(self, memory_id: str) -> dict | None:
        r = self.store.provenance(memory_id)
        return r.as_dict() if r else None

    # -- ecosystem composition ----------------------------------------------
    def ingest_gather(self, session: str, items: list[dict]) -> dict:
        """Ingest accountable-intake items (gather's shape) into memory, binding
        each item's origin receipt so the memory traces to its web source."""
        from .ingest import from_gather
        return from_gather(self, items, session)

    def provenance_chain(self, memory_id: str) -> dict | None:
        """The full re-checkable chain for a memory: atom -> source turn ->
        external origin receipt (web source + content hash)."""
        from .ingest import provenance_chain
        return provenance_chain(self, memory_id)

    def to_crucible(self, session: str | None = None, layer: str = "L1") -> dict:
        """Export memories as a crucible thesis + drift-derived measurements, so
        an independent judgment organ can certify the memory's faithfulness."""
        from .compose import to_crucible_thesis
        return to_crucible_thesis(self, session, layer)

    def consolidate(self, session: str | None = None, *, dup_threshold: float = 0.6,
                    apply: bool = True) -> dict:
        """Merge near-duplicate memories (audit-tombstoned) and surface
        contradiction candidates without auto-resolving them."""
        from .consolidate import consolidate
        return consolidate(self, session, dup_threshold=dup_threshold, apply=apply)

    # -- accountable editing -------------------------------------------------
    def forget(self, memory_id: str, reason: str = "") -> dict | None:
        """Delete a memory, leaving a tombstone in the hash-chained audit log:
        forgetting is auditable, not silent. None if the memory is absent."""
        return self.store.forget(memory_id, reason)

    def update(self, memory_id: str, new_text: str, reason: str = "") -> dict | None:
        """Edit a memory's text, keeping its provenance and recording the
        before/after hash in the audit log. None if the memory is absent."""
        return self.store.update(memory_id, new_text, reason)

    def audit(self) -> dict:
        """The append-only, hash-chained history of every forget/update, with a
        verify verdict. What was known and when it changed is re-checkable."""
        rows = self.store.audit_log()
        return {"schema": "mneme.audit/1", "entries": len(rows),
                "chain_intact": self.store.verify_audit(),
                "log": [{"op": r["op"], "memory_id": r["memory_id"],
                         "layer": r["layer"], "before_sha": r["before_sha"],
                         "after_sha": r["after_sha"], "reason": r["reason"],
                         "entry_sha": r["entry_sha"]} for r in rows]}

    # -- scenarios (L2) ------------------------------------------------------
    def build_scenarios(self, session: str, *, min_shared: int = 1) -> dict:
        """Cluster this session's atoms into L2 scenarios (scene blocks), each
        citing its member atoms so it stays drift-checkable. Deterministic."""
        from .scenario import cluster_atoms

        atoms = [{"id": a["id"], "text": a["text"]}
                 for a in self.store.memories(layer="L1", session=session)]
        scenarios = cluster_atoms(atoms, min_shared=min_shared)
        out = []
        for sc in scenarios:
            self.store.add_memory(sc.id, "L2", sc.text, sc.atom_ids, "cluster/v1",
                                  "atoms sharing a theme", session=session)
            out.append({"scenario_id": sc.id, "atoms": len(sc.atom_ids),
                        "theme": list(sc.theme)})
        return {"session": session, "scenarios": len(out), "blocks": out}

    # -- persona (L3) --------------------------------------------------------
    def persona(self, session: str) -> dict:
        """Synthesize a persona from this session's atoms. Deterministic floor:
        the atoms grouped, with each line bound to its source atom ids (so the
        persona is itself drift-checkable — L3 cites L2/L1, never free text)."""
        atoms = self.store.memories(layer="L1", session=session)
        lines = [a["text"] for a in atoms]
        source_ids = [a["id"] for a in atoms]
        text = "\n".join(f"- {ln}" for ln in lines)
        pid = content_hash(session, "persona", text)[:16]
        if lines:
            self.store.add_memory(pid, "L3", text, source_ids, "persona/v1",
                                  "profile synthesized from atoms", session=session)
        return {"session": session, "persona_id": pid if lines else None,
                "facts": len(lines), "text": text,
                "grounded_in": source_ids,
                "note": "persona cites its source atoms -> it is drift-checkable, not free text"}

    def close(self) -> None:
        self.store.close()
