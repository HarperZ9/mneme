# Changelog

## 0.4.1 (unreleased)

This release candidate adds native MCP Crucible export/replay operations after the public `v0.4.0` wheel. Those additions are not in the published `v0.4.0` wheel and must use a source checkout until a matching `v0.4.1` release asset exists.

- Add source-candidate MCP `mneme.to_crucible` and `mneme.replay_crucible` operations that reuse the existing Mneme export/replay library paths.
- Keep descriptors declarative: no executable commands, host state paths, or database paths in untrusted replay descriptors.
- Keep skipped-row replay templates refused until the contract includes a verifier-enforced full denominator for disclosed, skipped, and undisclosed rows.

## 0.4.0 (2026-09-13)

Additive release for explicit local origin freshness checks.

- Add `mneme origin-recheck MEMORY_ID --allowed-root DIR` plus
  `AgentMemory.recheck_local_origin(...)` and MCP `mneme.origin_recheck` for
  opt-in freshness checks of supported Gather `docs` / `file-read` receipts.
- Keep internal `drift()` semantics stable: stored-memory/source consistency is
  still separate from re-reading an external local origin.
- Compare Gather's normalized decoded text profile (`gather.docs.file-read/v1`),
  not raw bytes; legacy or unsupported origins return `UNVERIFIABLE` rather than
  becoming source-freshness proof.
- Open origin rechecks against Mneme state read-only and reject unsafe local path
  aliases, oversized reads, unsupported refs, invalid UTF-8 for the profile, and
  validation/open races.
- Preserve Crucible replay boundaries: replay still verifies sealed Mneme drift
  measurements from caller-owned SQLite snapshots and does not independently
  re-read external sources.

This is an additive release. It does not claim complete enterprise readiness,
network origin fetching, raw-byte integrity for Gather docs receipts, or new
database migrations.

## 0.3.0 (2026-09-07)

Release-prep candidate for source-turn partitioning and Gather interop.

- Partition named-user high-level source turn IDs by user/session, with a
  reserved `src:v1:` internal namespace.
- Preserve the default-user legacy raw-ID namespace while rejecting new
  default-user high-level writes that try to mint reserved internal IDs.
- Route Gather ingestion through the same source planner and add `user=...` /
  `mneme ingest --user` support while preserving existing callers that omit it.
- Reuse pre-partition named-user raw source rows only when ownership is
  unambiguous from current provenance; default-user citations now make a legacy
  raw source ambiguous for named-user reuse.
- Compare origin JSON semantically for compatibility so key order differences
  do not duplicate valid legacy Gather rows or reject identical current
  reimports.
- Skip exact high-level no-op source/memory writes so repeated same-byte imports
  do not advance source or memory ordinals.

This is not a historical database canonicalization release: it adds no real DB
migration and no new `turns` schema columns.

## 0.1.0 (unreleased)

First release. Accountable agent memory with layered storage, hybrid
retrieval, provenance, re-derivable recall, drift checks, a re-derivable
benchmark, and accountable forgetting.

- **4-tier memory** - L0 turns, L1 atoms (deterministic rule extraction), L2
  scenarios (union-find clustering), L3 persona; every layer cites its sources.
- **Hybrid retrieval** - BM25 (pure Python) fused with a vector channel by
  Reciprocal Rank Fusion; keyword / vector / hybrid. A **zero-dep local n-gram
  vector channel** (`embed="ngram"`) gives fuzzy/morphological matching out of
  the box (no embedding API); a real embedding model plugs in as an edge.
- **Recency-weighted recall** - prefer recent memories transparently; the
  recency component rides every hit and the rule is in the receipt.
- **Consolidation** - merge near-duplicate memories (audit-tombstoned) and
  surface contradiction candidates without auto-resolving them.
- **Multi-user / multi-session** - per-tenant isolation (`user=`) and
  cross-session recall (`user=X, session=None`); one user never recalls another's.
- **Entity graph** - grounded typed relations (lives_in, works_in, allergic_to,
  ...) + named entities, every edge citing its source atom (drift-checkable).
- **Temporal memory** - `supersede` keeps a changed fact's old value with a
  validity window, so `history` shows the timeline (Denver → Portland → Seattle)
  and `recall(as_of=N)` reconstructs the past; every transition is in the audit
  log. `forget` (GDPR erasure) still removes; `supersede` (a fact changed) keeps.
- **Provenance receipt** on every memory (sources, extractor, criterion, hash).
- **Re-derivable recall receipt** - ranked hits with bm25/vector/fused scores
  and the fusion rule; re-run the scorer, reproduce the ranking.
- **Self-flagging drift** - a memory whose source changed verdicts DRIFT; a
  missing source is UNVERIFIABLE.
- **Accountable forgetting** - forget/update leave a hash-chained tombstone;
  the deletion itself is auditable and tamper-evident.
- **Token-economics benchmark** - reduction AND answer-recall, re-derivable
  (built-in scenario: 76.6% reduction at 100% answer-recall).
- **Ecosystem composition** - ingest gather items so a recalled memory traces
  to its origin receipt (`mneme chain`); export schema-v2 Mneme drift measurements
  for Crucible to recompute and seal `MATCH`/`DRIFT`/`UNVERIFIABLE`. Independent
  source re-reading uses assessment-bound `mneme.recheck/1` descriptors and the
  zero-dependency `mneme replay-crucible` pack producer; descriptors contain no
  paths or commands (`mneme to-crucible`).
- **Tamper-honest source drift** - checks re-hash current turn or cited-memory
  fields, so direct SQLite byte edits cannot preserve a false `MATCH` by leaving
  a stale stored hash behind.
- **Strict replay provenance** - one decoder validates source-id lists and
  source-hash maps across drift, descriptor, and replay paths, closing JSON shape
  confusion such as `"ab"` versus `["a", "b"]`.
- **Immutable-snapshot mixed replay** - `replay-crucible` requires a
  caller-owned, quiescent, single-link rollback-journal snapshot without SQLite
  sidecars. It fingerprints that source around a consistent process-owned
  SQLite backup, reads only the private copy in immutable mode, consumes
  `crucible.replay-template/1`, and verifies a compact
  `crucible.replay-set/1` descriptor binding
  without descriptorless assessment rows, and atomically emits
  `crucible.replay-pack/1` without overwrite or an output alias of the database
  or its sidecars; invalid UTF-8, in-memory state, hardlinks, live sidecars,
  source changes detected during private-snapshot creation, and incompatible
  read-only schemas are named CLI errors rather than tracebacks. Later source
  changes cannot affect the process-owned replay copy. Library callers now use
  `read_only=True, immutable_snapshot=True`; ordinary `read_only=True` behavior
  remains available separately. Schema-less
  historical templates retain the complete measurement-seal fallback only when
  no replay binding is present. The documented snapshot recipe opens the source
  read-only: a read-write handle on a WAL database with an unclean shutdown
  recovers and checkpoints it, which rewrites the main file and removes both
  sidecars. Taking the snapshot read-only leaves the main file and the WAL
  byte-identical; the `-shm` index can still change, because SQLite readers
  coordinate through shared memory.
  An output path is refused for one of two named reasons, never a shared one:
  it resolves onto the state file or a sidecar, or Win32 normalizes it onto a
  different file than it spells. Ordinary `.` and `..` path components are not
  Win32 aliases and are accepted.
- **White-box inspector** - a self-contained HTML view of every layer with
  provenance, drift, and the audit log (`mneme inspect`).
- **MCP server** - tools over stdio; **runnable tour** (`examples/tour.py`).
- Zero runtime dependencies (stdlib sqlite3); deterministic; 100+ tests; CI on
  3 OS x 3 Python + a wheel-install job.
