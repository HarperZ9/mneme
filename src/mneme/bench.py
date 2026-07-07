"""bench.py — token-economics benchmark with a re-derivable number.

The category is won on one headline: "N% fewer tokens." Everyone publishes the
reduction; nobody publishes proof the answer SURVIVED the reduction. A memory
that hits 90% reduction by dropping the fact the agent needed is not efficient,
it is broken — and no competitor's benchmark can tell the two apart.

mneme measures both, re-derivably:

  token_reduction  1 - tokens(recalled context) / tokens(full history)
  answer_recall    fraction of probes whose needed fact IS in the recalled context

A reduction is only reported ALONGSIDE its answer_recall, so a number that looks
great by forgetting the answer is visibly disqualified. The receipt carries the
per-probe detail and the exact token estimator, so a third party re-runs the same
measurement over the same conversation and reproduces the number — a benchmark
you can escrow, not a marketing figure.

Zero-dep floor: token counting is a stated word/punctuation estimate; inject a
real tokenizer (e.g. tiktoken) via `token_fn` for exact counts. The reduction
RATIO is robust to the estimator; the receipt names which was used.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from .memory import AgentMemory
from .receipt import content_hash

_TOK = re.compile(r"\w+|[^\w\s]")


def estimate_tokens(text: str) -> int:
    """Zero-dep token estimate: words + standalone punctuation. Consistently
    proportional to real BPE counts, so the reduction RATIO is faithful even
    though the absolute count is an estimate (stated in the receipt)."""
    return len(_TOK.findall(text))


# a built-in reproducible scenario, so `mneme bench` yields a number out of the
# box (as the class ships benchmark figures). Each probe names the fact its
# answer requires, so answer_recall is checkable, not asserted.
_SCENARIO_TURNS = [
    {"role": "user", "text": "Hi, my name is Priya and I'm based in Austin, Texas."},
    {"role": "assistant", "text": "Hello Priya! How can I help?"},
    {"role": "user", "text": "I'm a vegetarian and I'm allergic to tree nuts."},
    {"role": "user", "text": "I work as a data engineer, mostly with Spark and Kafka."},
    {"role": "assistant", "text": "Got it."},
    {"role": "user", "text": "My partner and I are planning a trip to Japan in the spring."},
    {"role": "user", "text": "I strongly prefer window seats on flights."},
    {"role": "assistant", "text": "Noted, window seats."},
    {"role": "user", "text": "I use a mechanical keyboard and I code in Python and Scala."},
    {"role": "user", "text": "I'm training for a half marathon in October."},
]
_SCENARIO_PROBES = [
    {"query": "where is the user located", "answer_contains": "austin"},
    {"query": "dietary restrictions and allergies", "answer_contains": "allergic"},
    {"query": "what programming languages does the user use", "answer_contains": "python"},
    {"query": "seat preference on flights", "answer_contains": "window"},
    {"query": "what is the user training for", "answer_contains": "marathon"},
]


def run_bench(turns: list[dict] | None = None, probes: list[dict] | None = None,
              *, top_k: int = 3, token_fn: Callable[[str], int] | None = None,
              strategy: str = "keyword") -> dict:
    """Measure token reduction and answer recall for memory-injected context vs
    full history, over `turns` and `probes` (defaults to the built-in scenario).
    Each probe is {query, answer_contains}. Deterministic; returns a receipt."""
    turns = turns if turns is not None else _SCENARIO_TURNS
    probes = probes if probes is not None else _SCENARIO_PROBES
    tok = token_fn or estimate_tokens
    tok_name = "custom" if token_fn else "estimate/word+punct"

    mem = AgentMemory(":memory:")
    mem.remember("bench", turns)
    full_context = "\n".join(f"{t['role']}: {t['text']}" for t in turns)
    full_tokens = tok(full_context)

    per_probe = []
    recalled_total = 0
    answers_found = 0
    for p in probes:
        receipt = mem.recall(p["query"], strategy=strategy, top_k=top_k)
        recalled_text = "\n".join(h.text for h in receipt.hits)
        rt = tok(recalled_text)
        recalled_total += rt
        need = p["answer_contains"].lower()
        found = need in recalled_text.lower()
        answers_found += found
        per_probe.append({
            "query": p["query"], "answer_contains": p["answer_contains"],
            "answer_recalled": found, "recalled_tokens": rt,
            "hits": [h.memory_id for h in receipt.hits]})

    avg_recalled = recalled_total / max(len(probes), 1)
    reduction = 1.0 - (avg_recalled / full_tokens) if full_tokens else 0.0
    answer_recall = answers_found / max(len(probes), 1)
    payload = {
        "schema": "mneme.bench/1",
        "token_estimator": tok_name,
        "strategy": strategy, "top_k": top_k,
        "corpus": {"turns": len(turns), "atoms": len(mem.store.memories(layer="L1")),
                   "full_context_tokens": full_tokens},
        "token_reduction": round(reduction, 4),
        "answer_recall": round(answer_recall, 4),
        "avg_recalled_tokens": round(avg_recalled, 2),
        "probes": len(probes),
        "per_probe": per_probe,
        "honesty": ("token_reduction is only valid alongside answer_recall: a "
                    "reduction that drops the needed fact is disqualified, not a win"),
        "recheck": "mneme bench --state -  (re-run over the same conversation, reproduce the number)",
    }
    payload["receipt_sha256"] = content_hash(
        f"{reduction}", f"{answer_recall}", str(full_tokens),
        *[pp["query"] + str(pp["answer_recalled"]) for pp in per_probe])
    return payload
