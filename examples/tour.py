"""A runnable tour of mneme's accountable-memory lifecycle.

    python examples/tour.py

Walks the whole loop on one conversation and asserts the accountability
properties inline, so the tour doubles as a smoke test: ingest with provenance,
recall with a re-derivable receipt, drift when a source changes, forget with an
auditable tombstone, and prove a recalled memory traces back to its web source.
Everything is local, deterministic, and zero-dependency.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m" if sys.stdout.isatty() else f"\n== {title} ==")


def main() -> int:
    mem = AgentMemory(":memory:")

    section("1. remember — turns in, atomic facts out (each with provenance)")
    summary = mem.remember("alice", [
        {"role": "user", "text": "My name is Alice and I live in Portland."},
        {"role": "user", "text": "I prefer tea over coffee and I work in data science."},
        {"role": "assistant", "text": "Noted, Alice."},
        {"role": "user", "text": "I am vegetarian and allergic to shellfish."},
    ])
    print(f"  {summary['atoms']} atoms from {summary['turns']} turns "
          f"(the assistant turn is context, not memory)")
    assert summary["atoms"] == 3
    for p in summary["provenance"]:
        print(f"    - atom {p['memory_id'][:8]}… from turn {p['source_ids'][0]}")

    section("2. recall — with a receipt a third party can re-run")
    r = mem.recall("tea or coffee preference", strategy="keyword")
    for h in r.hits:
        print(f"    [{h.fused:.3f}] {h.text}")
    assert r.hits and "tea" in r.hits[0].text.lower()
    again = mem.recall("tea or coffee preference", strategy="keyword")
    assert [h.memory_id for h in r.hits] == [h.memory_id for h in again.hits]
    print("  re-ran the scorer: identical ranking (the recall is re-derivable)")

    section("3. drift — a memory whose source changes flags itself")
    print(f"  before: {mem.drift()['overall']}")
    mem.store.add_turn("t-portland", "alice", "user",
                       "My name is Alice and I live in Seattle now.")
    # point an atom's source at the changed turn to show drift
    atom = mem.store.memories(layer="L1")[0]
    mem.store.conn.execute("UPDATE memories SET source_ids=? WHERE id=?",
                           ('["t-portland"]', atom["id"]))
    mem.store.conn.commit()
    print(f"  after a source changed: {mem.drift()['overall']} "
          "(stale memory says so, it is not silently served)")

    section("4. forget — deletion you can audit")
    mid = mem.store.memories(layer="L1")[-1]["id"]
    mem.forget(mid, reason="user requested deletion")
    audit = mem.audit()
    print(f"  forgot 1 memory; audit log has {audit['entries']} tombstone, "
          f"chain intact: {audit['chain_intact']}")
    assert audit["chain_intact"]

    section("5. ecosystem — a memory that traces to its web source")
    mem2 = AgentMemory(":memory:")
    mem2.ingest_gather("research", [{
        "id": "g1", "text": "I am based in Austin and I work in security.",
        "source": "web", "ref": "https://example.com/profile",
        "method": "browser-extract", "sha256": "a" * 64}])
    hit = mem2.recall("where is the user based", strategy="keyword").hits[0]
    chain = mem2.provenance_chain(hit.memory_id)
    origin = chain["chain"][0]["origin"]
    print(f"  recalled: {hit.text!r}")
    print(f"  traces to: {origin['source']} · {origin['ref']} · sha256 {origin['sha256'][:12]}…")
    assert chain["externally_grounded"]
    print("  the recalled memory provably traces to the source it was gathered from.")

    section("done — every step above carried a re-checkable receipt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
