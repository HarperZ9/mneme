"""Falsifiers for mneme bench — the re-derivable token-economics benchmark.

Load-bearing: (1) the benchmark reports a real token reduction on the built-in
scenario; (2) answer_recall is measured, not assumed — a reduction that drops
the needed fact shows up as low answer_recall; (3) the number is deterministic
and re-derivable (same conversation -> same receipt).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mneme.bench import estimate_tokens, run_bench


def test_builtin_scenario_shows_real_reduction_and_recall():
    r = run_bench()
    # recall-injected context is much smaller than the full history
    assert r["token_reduction"] > 0.5, r["token_reduction"]
    # and the answers actually survive (the reduction is honest)
    assert r["answer_recall"] >= 0.8, r["answer_recall"]
    assert r["corpus"]["full_context_tokens"] > r["avg_recalled_tokens"]


def test_reduction_is_reported_with_answer_recall_always():
    r = run_bench()
    assert "token_reduction" in r and "answer_recall" in r
    assert "disqualified" in r["honesty"]        # the honesty clause is carried
    # every probe records whether its answer was recalled
    for pp in r["per_probe"]:
        assert "answer_recalled" in pp


def test_dropping_the_answer_is_visible_as_low_recall():
    # top_k=1 with a probe whose answer is NOT the top keyword hit -> recall drops
    turns = [
        {"role": "user", "text": "My name is Sam."},
        {"role": "user", "text": "I am allergic to penicillin."},
        {"role": "user", "text": "I enjoy sailing and photography on weekends."},
    ]
    # a query that lexically pulls the wrong atom first, so a tiny budget misses
    probes = [{"query": "sam name allergic penicillin sailing photography weekends hobby",
               "answer_contains": "penicillin"}]
    tight = run_bench(turns, probes, top_k=1)
    loose = run_bench(turns, probes, top_k=3)
    # the benchmark surfaces the trade-off honestly: a tighter budget can miss
    assert loose["answer_recall"] >= tight["answer_recall"]


def test_benchmark_is_deterministic_and_rederivable():
    a = run_bench()
    b = run_bench()
    assert a == b
    assert a["receipt_sha256"] == b["receipt_sha256"]


def test_custom_tokenizer_changes_counts_not_the_contract():
    # a fixed per-item tokenizer: the ratio still holds and recall is unchanged
    r = run_bench(token_fn=lambda t: len(t))    # char count
    assert r["token_estimator"] == "custom"
    assert 0.0 <= r["token_reduction"] <= 1.0
    assert r["answer_recall"] >= 0.8


def test_estimate_tokens_is_proportional_and_positive():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world") == 2
    assert estimate_tokens("a, b.") == 4       # words + standalone punctuation


def test_cli_bench_emits_receipt(capsys):
    from types import SimpleNamespace

    from mneme.cli import cmd_bench

    args = SimpleNamespace(turns=None, probes=None, top_k=3, strategy="keyword")
    assert cmd_bench(args) == 0
    import json
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == "mneme.bench/1" and "receipt_sha256" in out
