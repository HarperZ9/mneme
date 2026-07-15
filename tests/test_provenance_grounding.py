"""Falsifiers for the provenance-chain grounding verdict — it must not certify
an ungrounded or forged origin as externally grounded.

Load-bearing:
  1. a memory with NO source links is not externally_grounded (all() over an
     empty chain is vacuously true — the honest-null must win);
  2. a self-declared origin sha256 that is not even sha256-shaped is not a
     grounding (truthiness of a caller string is not a hash check).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory


def test_sourceless_memory_is_not_externally_grounded():
    m = AgentMemory(":memory:")
    m.store.add_memory("m1", "L1", "the operator approved the deploy", [],
                       "custom/v1", "atomic user fact")
    chain = m.provenance_chain("m1")
    assert chain["chain"] == []
    assert chain["externally_grounded"] is False


def test_forged_non_hex_sha_is_not_grounding():
    m = AgentMemory(":memory:")
    # an intake item with a bogus, non-hex "sha256" and no ref
    m.ingest_gather("s", [{"text": "X causes Y and I use it daily.", "sha256": "forged"}])
    atom = m.store.memories(layer="L1")[0]["id"]
    chain = m.provenance_chain(atom)
    assert chain["externally_grounded"] is False


def test_wellformed_origin_receipt_is_still_grounded():
    m = AgentMemory(":memory:")
    m.ingest_gather("r", [{"id": "g1", "text": "I am based in Austin and I work in security.",
                           "source": "web", "ref": "https://example.com/p",
                           "method": "http-get", "sha256": "a" * 64}])
    atom = m.store.memories(layer="L1")[0]["id"]
    assert m.provenance_chain(atom)["externally_grounded"] is True
