# mneme

> Accountable agent memory. The layered memory and hybrid retrieval agents
> expect, plus the three things no other memory system ships: every memory
> carries its provenance, every recall reproduces its ranking, and every stale
> memory flags its own drift.

`pip install mneme-memory` · zero runtime dependencies · fully local · deterministic

## Why another memory library

Agent memory systems store facts and hand them back. None of them can answer two
questions a serious deployment must ask:

- **Why did you recall *this* memory?** Their ranking is a black box.
- **Is this memory still true to its source?** They keep a fact after its source
  changed and you find out when the agent acts on stale information.

mneme answers both, because every operation emits a re-checkable receipt.

## The 4-tier memory (on par with the category)

```
L0 turn      raw dialogue                 -> stored verbatim
L1 atom      atomic user facts            -> extracted, each bound to its turn
L2 scenario  scene blocks of related atoms
L3 persona   the user profile             -> synthesized, citing its atoms
```

Retrieval is hybrid: BM25 (pure Python, always on) fused with an optional
embedding channel by Reciprocal Rank Fusion — the same keyword / semantic /
hybrid surface the leaders offer, with no required embedding API.

## What only mneme does

**A recall you can re-derive.** Every `recall` returns a receipt with the ranked
hits, their BM25 and vector scores, and the exact fusion rule. Re-run the scorer
over the same store and you get the identical ranking — the recall is auditable,
not asserted.

```bash
mneme remember alice session.json
mneme recall "where does the user live" --json
# -> {"schema":"mneme.recall/1","hits":[{"memory_id":"…","bm25":2.14,"fused":…}],
#     "recheck":"mneme recall --query Q --state DB  (re-run the scorer, reproduce the ranking)"}
```

**A memory that flags its own staleness.** `drift` re-derives every memory's
grounding against the current store: `MATCH` (source present and unchanged),
`DRIFT` (a source changed under the memory), `UNVERIFIABLE` (a source is gone).

```bash
mneme drift            # -> {"overall":"DRIFT","drifted":["…"], …}  exit 1 on drift
```

**Provenance on every memory.** Every atom names the turn it came from, the
extractor, the criterion, and a content hash. The persona is not free text — it
cites its atoms, so it is drift-checkable too.

## Library

```python
from mneme import AgentMemory

mem = AgentMemory("mem.db")                       # or ":memory:"
mem.remember("alice", [{"role": "user", "text": "I live in Denver and love dark roast."}])

receipt = mem.recall("coffee preference")         # RecallReceipt, re-derivable
print(mem.drift()["overall"])                     # MATCH until a source changes
```

An embedder (`AgentMemory(..., embedder=fn)`) turns on the vector channel; an
LLM `Extractor` plugs in for richer atoms. Neither is required — the
deterministic floor works with no model and no API.

## Agents plug in over MCP

```bash
mneme mcp          # JSON-RPC 2.0 over stdio; MNEME_STATE points at the DB
```

Tools: `mneme.remember`, `mneme.recall`, `mneme.drift`, `mneme.provenance`. A
recall through MCP returns the same re-derivable receipt, so the agent (or its
operator) can see and re-check why a memory was surfaced — the accountability
travels with the tool result.

## Scenarios (L2)

```bash
mneme scenarios alice     # cluster the session's atoms into scene blocks
```

Atoms sharing a theme cluster deterministically into L2 scenarios; each scenario
cites its atoms, so it is drift-checkable too (a scenario whose atom is gone is
`UNVERIFIABLE`, never silently kept).

## Guarantees

- **Zero runtime dependencies** (stdlib `sqlite3`). `pytest` is the only dev dep.
- **Deterministic.** No wall clock or randomness enters a stored hash or a
  ranking; the same turns rebuild the same memory, byte for byte.
- **Tests are the contract.** Every behavior above ships with a falsifier.

## License

MIT.
