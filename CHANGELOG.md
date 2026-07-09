# Changelog

## 0.1.0 (released 2026-07-07, tag v0.1.0)

First release. Accountable agent memory: the layered memory and hybrid retrieval
the category expects, plus provenance, re-derivable recall, self-flagging drift,
a re-derivable benchmark, and accountable forgetting.

- **4-tier memory** — L0 turns, L1 atoms (deterministic rule extraction), L2
  scenarios (union-find clustering), L3 persona; every layer cites its sources.
- **Hybrid retrieval** — BM25 (pure Python) fused with a vector channel by
  Reciprocal Rank Fusion; keyword / vector / hybrid. A **zero-dep local n-gram
  vector channel** (`embed="ngram"`) gives fuzzy/morphological matching out of
  the box (no embedding API); a real embedding model plugs in as an edge.
- **Recency-weighted recall** — prefer recent memories transparently; the
  recency component rides every hit and the rule is in the receipt.
- **Consolidation** — merge near-duplicate memories (audit-tombstoned) and
  surface contradiction candidates without auto-resolving them.
- **Multi-user / multi-session** — per-tenant isolation (`user=`) and
  cross-session recall (`user=X, session=None`); one user never recalls another's.
- **Entity graph** — grounded typed relations (lives_in, works_in, allergic_to,
  …) + named entities, every edge citing its source atom (drift-checkable).
- **Temporal memory** — `supersede` keeps a changed fact's old value with a
  validity window, so `history` shows the timeline (Denver → Portland → Seattle)
  and `recall(as_of=N)` reconstructs the past; every transition is in the audit
  log. `forget` (GDPR erasure) still removes; `supersede` (a fact changed) keeps.
- **Provenance receipt** on every memory (sources, extractor, criterion, hash).
- **Re-derivable recall receipt** — ranked hits with bm25/vector/fused scores
  and the fusion rule; re-run the scorer, reproduce the ranking.
- **Self-flagging drift** — a memory whose source changed verdicts DRIFT; a
  missing source is UNVERIFIABLE.
- **Accountable forgetting** — forget/update leave a hash-chained tombstone;
  the deletion itself is auditable and tamper-evident.
- **Token-economics benchmark** — reduction AND answer-recall, re-derivable
  (built-in scenario: 76.6% reduction at 100% answer-recall).
- **Ecosystem composition** — ingest gather items so a recalled memory traces
  to its web source (`mneme chain`); export memories as a crucible thesis so an
  independent organ certifies their faithfulness (`mneme to-crucible`).
- **White-box inspector** — a self-contained HTML view of every layer with
  provenance, drift, and the audit log (`mneme inspect`).
- **MCP server** — 6 tools over stdio; **runnable tour** (`examples/tour.py`).
- Zero runtime dependencies (stdlib sqlite3); deterministic; 82 tests; CI on
  3 OS × 3 Python + a wheel-install job.
