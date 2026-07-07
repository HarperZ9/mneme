# Changelog

## 0.1.0 (unreleased)

First release. Accountable agent memory: the layered memory and hybrid retrieval
the category expects, plus provenance, re-derivable recall, self-flagging drift,
a re-derivable benchmark, and accountable forgetting.

- **4-tier memory** — L0 turns, L1 atoms (deterministic rule extraction), L2
  scenarios (union-find clustering), L3 persona; every layer cites its sources.
- **Hybrid retrieval** — BM25 (pure Python) fused with an optional embedding
  channel by Reciprocal Rank Fusion; keyword / vector / hybrid.
- **Provenance receipt** on every memory (sources, extractor, criterion, hash).
- **Re-derivable recall receipt** — ranked hits with bm25/vector/fused scores
  and the fusion rule; re-run the scorer, reproduce the ranking.
- **Self-flagging drift** — a memory whose source changed verdicts DRIFT; a
  missing source is UNVERIFIABLE.
- **Accountable forgetting** — forget/update leave a hash-chained tombstone;
  the deletion itself is auditable and tamper-evident.
- **Token-economics benchmark** — reduction AND answer-recall, re-derivable
  (built-in scenario: 76.6% reduction at 100% answer-recall).
- **MCP server** — remember/recall/drift/provenance/forget/audit over stdio.
- Zero runtime dependencies (stdlib sqlite3); deterministic; 30 tests.
