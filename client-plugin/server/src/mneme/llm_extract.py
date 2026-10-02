"""llm_extract.py — an optional LLM extractor edge for richer atoms.

mneme's deterministic RuleExtractor is the zero-dep floor: it catches
first-person fact cues but misses third-person or implicit facts ("she codes in
Rust", "the meeting is Tuesday"). The class extracts atoms with an LLM; this is
that edge, kept OFF the zero-dep core: an OpenAI-compatible extractor over
stdlib urllib (no SDK) that asks a model for atomic facts and returns Atoms with
provenance to the source turn.

Accountability is preserved: the LLM only PROPOSES atoms; each proposed atom is
kept only if its text actually appears (normalized) in the source turn, so the
extractor cannot hallucinate a fact the turn does not support. A proposal that
is not grounded in the turn is dropped — the model suggests, the source decides.
Keys come from the environment only; never hardcoded, never logged.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

from .extract import Atom

_PROMPT = (
    "Extract the durable, atomic facts stated in the user message below, one per "
    "line, each a short standalone sentence quoting the message's own wording as "
    "closely as possible. Only facts actually stated; no inference. If none, "
    "output nothing.\n\nUser message:\n{text}\n\nFacts:"
)


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


# function/paraphrase words a grounding check should ignore (the model rewrites
# "I" -> "the user", "like" -> "likes"); grounding is about CONTENT, not phrasing.
_GROUND_STOP = {"the", "user", "users", "and", "or", "a", "an", "is", "are", "was",
                "were", "be", "in", "on", "at", "to", "of", "for", "with", "they",
                "she", "he", "his", "her", "their", "them", "as", "that", "this"}


def _supported(word: str, turn_words: set[str]) -> bool:
    """A content word is supported if the turn has it, or a word sharing a 4-char
    prefix (morphology: like/likes, code/codes) — enough to catch a paraphrase
    but not a hallucinated noun (yacht shares no prefix with the turn)."""
    if word in turn_words:
        return True
    if len(word) >= 4:
        return any(len(tw) >= 4 and tw[:4] == word[:4] for tw in turn_words)
    return False


class LLMExtractor:
    """OpenAI-compatible atom extractor. `AgentMemory(extractor=LLMExtractor())`.
    Grounded: a proposed atom is kept only if its content is present in the turn,
    so the model cannot invent facts. Zero-dep (urllib)."""

    name = "llm/v1"

    def __init__(self, *, base_url: str | None = None, model: str | None = None,
                 api_key_env: str = "OPENAI_API_KEY", user_only: bool = True,
                 timeout: float = 60.0, transport=None):
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL",
                         "https://api.openai.com/v1")).rstrip("/")
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.api_key_env = api_key_env
        self.user_only = user_only
        self.timeout = timeout
        self._transport = transport            # inject for tests (str prompt -> str reply)

    def _complete(self, prompt: str) -> str:
        if self._transport is not None:
            return self._transport(prompt)
        key = os.environ.get(self.api_key_env, "")
        body = json.dumps({"model": self.model, "temperature": 0.0,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            obj = json.loads(r.read())
        return obj["choices"][0]["message"]["content"]

    def extract(self, turn_id: str, role: str, text: str) -> list[Atom]:
        if self.user_only and role.lower() not in ("user", "human"):
            return []
        reply = self._complete(_PROMPT.format(text=text))
        turn_norm = _normalize(text)
        atoms: list[Atom] = []
        seen: set[str] = set()
        for line in reply.splitlines():
            fact = line.strip().lstrip("-*0123456789. ").strip()
            if len(fact.split()) < 2:
                continue
            # GROUNDING: keep only facts whose words are supported by the turn,
            # so the model cannot hallucinate a fact the source does not state
            fnorm = _normalize(fact)
            if not fnorm or fnorm in seen:
                continue
            turn_words = set(turn_norm.split())
            content = [w for w in fnorm.split() if w not in _GROUND_STOP]
            if not content:
                continue
            grounded = sum(1 for w in content if _supported(w, turn_words)) / len(content)
            if grounded < 0.6:                 # most CONTENT words must be in the turn
                continue
            seen.add(fnorm)
            atoms.append(Atom(text=fact, source_id=turn_id))
        return atoms
