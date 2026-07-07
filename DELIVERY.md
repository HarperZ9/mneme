# Delivery checklist — mneme 0.1.0

Everything below is verified. Publishing is the operator's call (outward-facing,
irreversible); this turns it into a reviewed, pre-flighted, four-command action.

## Preflight (verified 2026-07-07)

- [x] **Tests green** — 42 falsifiers pass (`python -m pytest -q`).
- [x] **Zero runtime dependencies** — stdlib only; `pytest` is the sole dev dep.
- [x] **No secrets** — credential scan of `src/`+`tests/` clean; no `.env`, `.db`,
      `.key`, or `.token` tracked (`.gitignore` covers them).
- [x] **Clean working tree** — no uncommitted changes.
- [x] **Wheel builds and runs** — `mneme_memory-0.1.0-py3-none-any.whl` built,
      installed in a fresh venv with no `src` on path; `mneme --version`,
      `mneme bench` (76.6% / 100% recall), and all 6 MCP tools resolve from the
      installed package.
- [x] **LICENSE** (MIT) and **CHANGELOG** present; `pyproject.toml` metadata
      complete (name `mneme-memory`, console script `mneme`, urls).
- [x] **CI written** — `.github/workflows/ci.yml`: pytest on ubuntu/windows/macos
      × py3.11–3.13 + a wheel-install job.

## The four commands (operator-gated)

```bash
# 1. create the public repo (decides visibility)
gh repo create HarperZ9/mneme --public --source . --remote origin --description \
  "Accountable agent memory: layered memory + hybrid retrieval where every memory carries provenance, every recall is re-derivable, every stale memory flags its own drift."

# 2. push (CI runs on arrival)
git push -u origin main

# 3. build the distribution
python -m build          # -> dist/mneme_memory-0.1.0-{whl,tar.gz}

# 4. publish to PyPI (needs a PyPI token; prefer OIDC trusted publishing via CI)
python -m twine upload dist/*
```

## Positioning (for the release notes)

Category: agent long-term memory (vs Mem0, TencentDB-Agent-Memory, Zep). mneme
matches the class's surface — 4-tier L0–L3, hybrid BM25+vector retrieval, MCP,
memory edit/delete — and adds what none of them ship: provenance on every
memory, a re-derivable recall receipt, self-flagging drift, auditable forgetting,
a re-derivable token benchmark (76.6% reduction at 100% answer-recall, vs the
category's 61% headline that does not prove the answer survived), and the
ecosystem moat — a recalled memory that provably traces back to the web source
it was gathered from. Zero-dep, deterministic, MIT.
