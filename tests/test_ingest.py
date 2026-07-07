"""Falsifiers for the ecosystem composition: gather intake -> mneme memory with
an unbroken, re-checkable provenance chain from the web source to the memory.

Load-bearing: (1) a memory ingested from gather traces back through its source
turn to the origin receipt (source, ref/url, sha256); (2) the chain is honest
about native (non-external) turns; (3) a malformed item is skipped with a
reason, never guessed. Includes a real gather Item when gather is importable.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from mneme import AgentMemory

# gather's Provenance shape, as the dicts an intake tool emits
GATHER_ITEMS = [
    {"id": "g1", "text": "The user is based in Austin and works in security.",
     "source": "web", "ref": "https://example.com/profile", "method": "browser-extract",
     "sha256": "a" * 64},
    {"id": "g2", "text": "I prefer window seats and I am vegetarian.",
     "source": "web", "ref": "https://example.com/prefs", "method": "http-get",
     "sha256": "b" * 64},
]


def test_ingested_memory_chains_back_to_the_web_source():
    m = AgentMemory(":memory:")
    summary = m.ingest_gather("research", GATHER_ITEMS)
    assert summary["atoms"] >= 2
    atom_id = summary["provenance"][0]["memory_id"]
    chain = m.provenance_chain(atom_id)
    assert chain["externally_grounded"] is True
    link = chain["chain"][0]
    assert link["origin"]["ref"].startswith("https://")   # the web url survives
    assert link["origin"]["sha256"] and link["origin"]["source"] == "web"
    assert "re-fetch" in chain["recheck"]                 # the chain is re-checkable


def test_native_turn_memory_is_honestly_not_externally_grounded():
    m = AgentMemory(":memory:")
    m.remember("s", [{"id": "t1", "role": "user", "text": "I live in Denver."}])
    atom = m.store.memories(layer="L1")[0]
    chain = m.provenance_chain(atom["id"])
    assert chain["externally_grounded"] is False          # no external origin, said plainly
    assert chain["chain"][0]["origin"] is None


def test_malformed_item_is_skipped_with_a_reason():
    m = AgentMemory(":memory:")
    summary = m.ingest_gather("s", [{"id": "bad", "source": "web"}])   # no text
    assert summary["ingested"] == 0
    assert summary["skipped"] and summary["skipped"][0]["reason"] == "item has no text"


def test_ingest_is_idempotent_by_content():
    m = AgentMemory(":memory:")
    a = m.ingest_gather("s", GATHER_ITEMS)["atoms"]
    b = m.ingest_gather("s", GATHER_ITEMS)["atoms"]        # same items again
    assert len(m.store.memories(layer="L1")) == a          # no duplicates on re-ingest


def test_chain_survives_a_real_gather_item_if_gather_is_available():
    try:
        sys.path.insert(0, str(Path("C:/dev/public/gather/src")))
        from gather.item import Item, Provenance
    except Exception:
        pytest.skip("gather not importable here")
    prov = Provenance(source="web", ref="https://example.com/x", method="http-get",
                      fetched_at=0.0, sha256="c" * 64)
    item = Item(kind="metadata", id="real1", title="t",
                text="I code in Python and I am based in Austin.", provenance=prov)
    as_dict = {"id": item.id, "text": item.text, "source": item.provenance.source,
               "ref": item.provenance.ref, "method": item.provenance.method,
               "sha256": item.provenance.sha256}
    m = AgentMemory(":memory:")
    summary = m.ingest_gather("r", [as_dict])
    chain = m.provenance_chain(summary["provenance"][0]["memory_id"])
    assert chain["externally_grounded"]
    assert chain["chain"][0]["origin"]["sha256"] == "c" * 64


def test_recalled_memory_still_carries_the_chain():
    # the payoff: recall a memory, then prove where it came from
    m = AgentMemory(":memory:")
    m.ingest_gather("r", GATHER_ITEMS)
    r = m.recall("where is the user based", strategy="keyword")
    assert r.hits
    chain = m.provenance_chain(r.hits[0].memory_id)
    assert chain is not None and chain["externally_grounded"]
