# Changelog

## 0.5.0 (2026-09-26)

BREAKING. `forget` becomes a true forget, the MCP forget tool takes two steps,
replay snapshots move to a per-user state directory, and schema 5 blinds the
update and supersede history in the audit log.

- `forget` erases the memory, its source turns and every derived form:
  memories that cite an erased turn or memory, scenario and persona rows, and
  the fact's supersession history in both directions. `include_sources=False`
  (CLI `--keep-sources`) keeps the source turns. Memories that only share a
  source turn with the target are collateral: the library raises
  `CollateralError` with the plan unless `allow_collateral=True`, and the CLI
  asks, or refuses `--yes` without `--allow-collateral`. The CLI also erases
  by `--turn` or `--session`.
- The plan also takes duplicates: turns and memories of the same user that
  repeat an erased text whole, with what derives from them. They need the same
  consent as collateral. A turn belongs to the users of the memories that cite
  it, else of the memories in its session; a turn in a session with no
  memories counts only when the erase touches that session, so another user's
  turn is never offered. Consolidation now records a merge link (`merges`
  table) when it merges a near-duplicate away, and an erase of the kept memory
  takes the merged row's source turns too. The plan digest binds each row's
  content hash, so an edit between plan and confirm makes the plan stale.
- One transaction deletes the rows and appends one `erase` audit entry per
  row, with `secure_delete` on. An entry names a random erase ref, never the
  content-derived id, and stores a salted commitment to the erased text whose
  salt is never stored; `mneme forget --emit-opening` prints the salts once,
  only to a terminal, and refuses on a pipe before deleting anything. A reason
  that repeats a 16-character run of erased text, holds one of its words of 8
  or more characters (6 with a digit), is itself a short piece of it, or names
  an erased row's id, content hash or the plan digest (any 12-character run),
  is refused, because reasons are stored verbatim. The check catches verbatim
  repeats only.
- After the commit, this store's replay snapshots are removed, a WAL store is
  checkpointed, the file is vacuumed with `temp_store=MEMORY`, and the
  database files are scanned for erased bytes. A refused erase changes
  nothing, snapshots included. The
  receipt reports the scan and, beside it, the residue the erase cannot remove
  (earlier audit rows that name erased rows by content-derived id, audit
  reasons that quote erased text, other users' rows that repeat it, freed disk
  blocks, texts under 16 bytes, which get structural checks only) and the
  copies out of its reach (exports, backups, other copies of the file, text
  sent to a model provider, the MCP client's own session history, replay
  snapshots older versions left in the OS temp directory, and other stores'
  snapshots, which are scanned). The receipt carries no plan digest and no
  local path; the CLI prints the paths of legacy temp snapshots to stderr.
- The receipt's `findings` lists a code for each check that did not pass
  (the README maps each code to a status), and `status` is `erased` only when
  the list is empty. Otherwise it is `erased_residue_found`,
  `erased_copies_remain`, `incomplete` (a file could not be read) or
  `erased_sources_kept`, and `erased_unverified` when a step after the commit
  failed. A kept row that holds a short erased text, or shares a 16-character
  run with a long one, is residue. A failure after the commit is no longer
  reported as a refusal. The CLI exits 3 for every status but `erased` and
  `erased_sources_kept`.
- The erase transaction sets `meta.erase_pending`. It is cleared only when
  the scrub and the snapshot removal both finished. While it is set, status
  and doctor warn, and `mneme scrub` finishes the work, removing the store's
  replay snapshots too, and says whether the erase ran its residual scan.
- The residual scan no longer caps the places it checks for each word, so kept
  rows that share words with residue cannot hide it. Only turns, memories and
  merge links count as kept, so text in any other table is reported. A link
  is never listed as a snapshot, and no file in the shared temp directory is
  read past 1 GiB. On POSIX such a file is also opened without following
  links or blocking, and skipped when another user owns it; Windows has no
  equivalent flags or owner ids here, so those checks do not run there.
- A snapshot directory that cannot be listed counts as a failed removal
  instead of reading as empty. A snapshot name with an out-of-range process id
  is `unknown` and never swept, and a malformed merge row no longer stops an
  erase.
- `AgentMemory.forget` returns the erase receipt instead of one audit entry.
  `Store.forget` stays the row-level primitive consolidation uses.
- MCP `mneme.forget` returns the plan and deletes nothing unless the call
  carries `confirm_plan_sha256`. It returns ids and counts, text previews only
  with `include_previews`, and never an opening. The plan carries the number
  of users, not their names. Applying a plan with collateral or duplicate rows
  needs `allow_collateral: true`.
- The CLI plan shows row text and user names only on a terminal or with
  `--show-text`, and
  `--dry-run` opens the database read-only. End of input at the prompt
  refuses instead of raising.
- Replay snapshots live in `<LocalAppData>/mneme/snapshots` on Windows and in
  `$XDG_STATE_HOME/mneme/snapshots` (or `~/.local/state/mneme/snapshots`)
  elsewhere, under the store's random `store_id`, never beside the database.
  Snapshots whose process is gone are swept when a writable store opens, when
  the MCP server starts, when a snapshot is made and when an erase starts.
- `meta.schema_high_water` records the highest schema that wrote a database.
  A lower `schema_version` on open means an older mneme reopened it: mneme
  warns, keeps the finding in `meta.schema_downgrade_seen`, and migrates
  again. Do not share one database file across mneme versions.
- Schema 5 blinds audit history. `update` and `supersede` entries store
  `b1:` + sha256(tag, salt, content hash) for each version instead of the
  plain content hash, with a fresh salt per value in a new `salts` table under
  the memory it describes. An erase deletes those salts in its transaction,
  and the receipt reports `salts_deleted` and counts earlier rows as `blinded`
  or `unsalted_hashes`. The row-level `forget` tombstone is blinded with a salt
  that is never stored, and it deletes the row's salts. Entries written before
  schema 5 keep their plain hashes, since rewriting them would break the chain.
  All these entries still name the memory by its content-derived id. A
  supersede without a reason stores "superseded" instead of the new version's
  id, and a consolidation reason no longer names the kept memory's id.
  An `update` entry's `after_sha` no longer equals the row's `content_sha256`;
  `audit_blind.opens(conn, value, content_sha256)` checks it instead.
- The row-level `forget`, `update` and `supersede` now write their audit entry
  in the same transaction as the row change, so a crash cannot leave an audit
  record of a change that never happened. A failure before the commit rolls
  the row change, its salts and its entry back together.
- `mneme status` and `mneme doctor` print the database's absolute path,
  whether it is the default `mneme.db` in the current directory, its files,
  row counts and schema history, and the replay snapshot directory with
  counts. Both open the database read-only and never create it. They warn
  when the database sits inside a git work tree, when an older mneme reopened
  it, when replay snapshots were left behind, when an erase has not finished,
  and when the state keeps nothing (`:memory:` or an empty path). They count
  unsalted forget entries and turns no memory cites, which a forget before
  0.5.0 leaves. `doctor` also re-derives the audit chain and exits 1 on any
  warning.
- MCP `mneme.doctor` adds `state_path_absolute`, `state_from_env`, `kind`,
  `default_location`, `exists`, `git_work_tree`, `snapshot_dir`, `warnings`
  and `notes`, and never opens the database. `state_path` keeps its meaning:
  the configured value. The home directory is written as `~` in its paths.
- Docs, docstrings and runtime strings no longer call forget a legal erasure;
  they say what it removes and what it leaves. The README no longer calls
  mneme fully local: the store stays local, and text sent to an extractor,
  embedder or MCP client goes to that model's provider.
- Publication mode of `verify_release_metadata.py` fails when README or
  USAGE still calls the tagged version unreleased. The 0.1.0 entry below keeps its
  wording, followed by a dated correction.

## 0.4.2 (2026-09-22)

Publish to PyPI as `flywheel-mneme`. The install command changes, so this is a
release rather than a metadata edit.

`mneme-memory` on PyPI belongs to an unrelated project described as a portable
memory layer for AI agents, which is close enough to this package's purpose to be
a real confusion hazard. `flywheel-mneme` is the HarperZ9 distribution, published
with PEP 740 attestations recording which workflow built the bytes.

The import name, the module layout and the `mneme` console script are unchanged.
The `mneme-memory:` prefix inside composed evidence ids is an evidence identifier
format rather than a package reference, and is deliberately left as it is.

## 0.4.1 (2026-09-17)

Add native MCP Crucible export/replay operations after the public `v0.4.0` wheel. The `v0.4.1` package includes those MCP tools with the same bounded replay contracts as the CLI and library paths.

- Add MCP `mneme.to_crucible` and `mneme.replay_crucible` operations that reuse the existing Mneme export/replay library paths.
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
  Correction, 2026-09-26: this `forget` deleted the memory row only. The raw
  turn and the rows derived from it stayed, so the label above overstated it.
  From 0.5.0, `forget` erases the source turns and every derived form, and its
  receipt names what stays (see the 0.5.0 entry).
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
