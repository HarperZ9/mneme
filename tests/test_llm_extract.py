"""Falsifiers for the LLM extractor edge.

Load-bearing: (1) it extracts richer atoms (third-person facts the rule floor
misses); (2) GROUNDING — a hallucinated fact not supported by the turn is
DROPPED, so the model cannot invent memories; (3) it drops into AgentMemory as
the extractor and the atoms keep provenance; (4) against a LIVE mock OpenAI
server, no secret leaks and the protocol shape is correct.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from mneme import AgentMemory
from mneme.llm_extract import LLMExtractor


def test_extracts_third_person_fact_the_rule_floor_misses():
    # a canned model reply; grounded facts survive
    reply = "The user codes in Rust.\nThe user lives in Berlin."
    ex = LLMExtractor(transport=lambda _p: reply)
    atoms = ex.extract("t1", "user", "The user codes in Rust and lives in Berlin.")
    texts = [a.text for a in atoms]
    assert any("rust" in t.lower() for t in texts)   # rule floor would miss 'codes'
    assert all(a.source_id == "t1" for a in atoms)


def test_hallucinated_fact_not_in_the_turn_is_dropped():
    # the model invents a fact the turn never states -> grounding rejects it
    reply = "The user lives in Berlin.\nThe user owns three yachts."
    ex = LLMExtractor(transport=lambda _p: reply)
    atoms = ex.extract("t1", "user", "I live in Berlin.")
    texts = " ".join(a.text.lower() for a in atoms)
    assert "berlin" in texts
    assert "yacht" not in texts                       # ungrounded -> dropped


def test_drops_into_agent_memory_as_the_extractor():
    reply = "The user works as a chef.\nThe user is based in Lyon."
    m = AgentMemory(":memory:", extractor=LLMExtractor(transport=lambda _p: reply))
    m.remember("s", [{"id": "t1", "role": "user",
                      "text": "The user works as a chef and is based in Lyon."}])
    atoms = m.store.memories(layer="L1")
    assert len(atoms) == 2
    assert m.provenance(atoms[0]["id"])["extractor"] == "llm/v1"
    # recall works over LLM-extracted atoms
    assert m.recall("occupation chef", strategy="keyword").hits


def test_assistant_turns_skipped_by_default():
    ex = LLMExtractor(transport=lambda _p: "Something.")
    assert ex.extract("t1", "assistant", "The user codes in Rust.") == []


def test_against_a_live_mock_openai_server_no_secret_leak(monkeypatch):
    seen = {}

    class _Mock(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["auth"] = self.headers.get("Authorization", "")
            seen["model"] = body["model"]
            out = json.dumps({"choices": [{"message": {"content": "The user likes tea."}}]})
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.end_headers(); self.wfile.write(out.encode())

        def log_message(self, *a):
            pass

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    srv = HTTPServer(("127.0.0.1", 0), _Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ex = LLMExtractor(base_url=f"http://127.0.0.1:{srv.server_port}/v1",
                          model="test-model")
        atoms = ex.extract("t1", "user", "I like tea.")
        assert atoms and "tea" in atoms[0].text.lower()
        assert seen["model"] == "test-model"
        assert seen["auth"] in ("", "Bearer ")        # no invented secret
    finally:
        srv.shutdown()
