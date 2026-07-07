"""Falsifiers for multi-user / multi-session scoping.

Load-bearing: (1) memories are isolated by user (one tenant never recalls
another's); (2) recall can scope to a session OR span all of a user's sessions
(cross-session recall); (3) it is backward compatible (default shared user).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme import AgentMemory


def _multi():
    m = AgentMemory(":memory:")
    m.remember("chat1", [{"role": "user", "text": "I live in Denver."}], user="alice")
    m.remember("chat2", [{"role": "user", "text": "I prefer tea."}], user="alice")
    m.remember("chat1", [{"role": "user", "text": "I live in Boston."}], user="bob")
    return m


def test_users_are_isolated():
    m = _multi()
    alice = m.recall("where does the user live", strategy="keyword", user="alice")
    assert alice.hits and "denver" in alice.hits[0].text.lower()
    assert all("boston" not in h.text.lower() for h in alice.hits)   # never bob's
    bob = m.recall("where does the user live", strategy="keyword", user="bob")
    assert bob.hits and "boston" in bob.hits[0].text.lower()
    assert all("denver" not in h.text.lower() for h in bob.hits)


def test_cross_session_recall_spans_a_users_sessions():
    m = _multi()
    # alice's tea preference lives in chat2, her location in chat1 — one recall
    # scoped to the user (session=None) sees both
    across = m.recall("tea preference", strategy="keyword", user="alice")
    assert across.hits and "tea" in across.hits[0].text.lower()
    # scoping to a single session sees only that session
    just_chat1 = m.recall("tea preference", strategy="keyword",
                          user="alice", session="chat1")
    assert all("tea" not in h.text.lower() for h in just_chat1.hits)


def test_user_list_and_scoped_counts():
    m = _multi()
    assert m.store.users() == ["alice", "bob"]
    assert len(m.store.memories(layer="L1", user="alice")) == 2
    assert len(m.store.memories(layer="L1", user="bob")) == 1


def test_backward_compatible_default_user():
    m = AgentMemory(":memory:")
    m.remember("s", [{"role": "user", "text": "I live in Denver."}])   # no user
    # default-user recall (user=None spans all) still works
    r = m.recall("where does the user live", strategy="keyword")
    assert r.hits and "denver" in r.hits[0].text.lower()
    assert m.store.users() == [""]                # the shared default user
