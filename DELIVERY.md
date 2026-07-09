# Delivery checklist — mneme 0.1.0

Everything below is verified. Publishing is the operator's call (outward-facing,
irreversible); this turns it into a reviewed, pre-flighted, four-command action.

## Preflight (verified 2026-07-07)

- [x] **Tests green** — 82 falsifiers pass (`python -m pytest -q`, re-verified 2026-07-09).
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

## Status

- [x] **GitHub: LIVE** — https://github.com/HarperZ9/mneme (public, pushed 2026-07-07).
- [ ] **PyPI** — one step away, tokenless via OIDC. Do this once on PyPI, then tag:

```bash
# one-time on PyPI: add a trusted publisher
#   project: mneme-memory · owner: HarperZ9 · repo: mneme · workflow: release.yml
# then a tag publishes automatically (no token, ever):
git tag v0.1.0 && git push origin v0.1.0
```

The `release.yml` workflow builds, verifies the tag matches the version,
installs the wheel and smokes the CLI, then publishes via OIDC. No token passes
through any tool but PyPI's own trusted-publisher handshake.

## Positioning (for the release notes)

Category: agent long-term memory (vs Mem0, TencentDB-Agent-Memory, Zep). mneme
matches the class's surface — 4-tier L0–L3, hybrid BM25+vector retrieval, MCP,
memory edit/delete — and adds what none of them ship: provenance on every
memory, a re-derivable recall receipt, self-flagging drift, auditable forgetting,
a re-derivable token benchmark (76.6% reduction at 100% answer-recall, vs the
category's 61% headline that does not prove the answer survived), and the
ecosystem moat — a recalled memory that provably traces back to the web source
it was gathered from. Zero-dep, deterministic, MIT.
