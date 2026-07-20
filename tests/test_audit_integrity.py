"""Falsifiers for audit-chain integrity — a forward-only hash walk is not enough.

The audit log's whole promise is 'you cannot quietly forget that you forgot
something'. Two ways that promise leaked:
  1. TAIL TRUNCATION — deleting the most recent tombstone (or emptying the log)
     leaves a self-consistent prefix that a forward-only walk accepts.
  2. DELIMITER COLLISION — hashing fields pre-joined by '|' lets a byte shift
     across a field boundary (reason is free text and may contain '|') reproduce
     the same entry hash.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory

TURNS = [
    {"id": "t1", "role": "user", "text": "My name is Dana and I live in Denver."},
    {"id": "t2", "role": "user", "text": "I prefer dark roast coffee."},
]


def _mem():
    m = AgentMemory(":memory:")
    m.remember("s", TURNS)
    return m


def test_tail_truncation_is_detected():
    m = _mem()
    ids = [r["id"] for r in m.store.memories(layer="L1")]
    m.forget(ids[0], reason="one")
    m.update(ids[1], "two", reason="two")
    assert m.store.verify_audit() is True
    # delete the most recent tombstone; the surviving prefix still chains cleanly
    m.store.conn.execute("DELETE FROM audit WHERE ord=(SELECT MAX(ord) FROM audit)")
    m.store.conn.commit()
    assert m.store.verify_audit() is False
    assert m.audit()["chain_intact"] is False


def test_emptying_the_log_after_ops_is_detected():
    m = _mem()
    mid = m.store.memories(layer="L1")[0]["id"]
    m.forget(mid, reason="gone")
    m.store.conn.execute("DELETE FROM audit")
    m.store.conn.commit()
    assert m.store.verify_audit() is False


def test_delimiter_boundary_shift_is_detected():
    m = _mem()
    mid = m.store.memories(layer="L1")[0]["id"]
    m.forget(mid, reason="dup|cleanup")
    assert m.store.verify_audit() is True
    # shift a byte across the before_sha/after_sha/reason boundary: a pipe-joined
    # hash reads (before+'|', 'dup', 'cleanup') identically to (before, '', 'dup|cleanup')
    m.store.conn.execute(
        "UPDATE audit SET before_sha = before_sha || '|', after_sha='dup', reason='cleanup' "
        "WHERE ord=(SELECT MIN(ord) FROM audit)")
    m.store.conn.commit()
    assert m.store.verify_audit() is False
