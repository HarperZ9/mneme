# Delivery checklist - mneme releases

## 0.4.1 release candidate preparation (2026-09-17)

The public `v0.4.0` GitHub wheel remains the released local-origin freshness package. This source candidate adds MCP Crucible export/replay behavior after that release and uses the next unused patch version, `0.4.1`, so those additions are not conflated with the public `v0.4.0` assets. Do not describe the MCP Crucible export/replay additions as present in the public `v0.4.0` wheel.

Preflight scope for this candidate:

- [ ] Version metadata aligned at `0.4.1` (`pyproject.toml`, `mneme.__version__`,
      changelog top entry, and version-alignment test).
- [ ] Source tests green on the exact release-candidate branch.
- [ ] Wheel builds and installs in a clean environment; installed
      `mneme --version` reports `0.4.1`.
- [ ] Installed CLI and MCP smoke cover the existing synthetic memory flow,
      malformed input controls, and MCP Crucible export/replay behavior.

Publication boundary:

- No `v0.4.1` tag, GitHub Release, or PyPI publication exists yet.
- PyPI remains controlled by the repository `PYPI_ENABLED` variable and PyPI
  trusted publishing.
- Publishing requires merge to `main`, exact-head CI, an unpublished matching
  tag or GitHub Release event, and the release workflow gates below.

## 0.4.0 release preparation (2026-09-13)

This record binds the checked gates for the additive local-origin freshness
release prepared after PR #13. It describes the already-published public
`v0.4.0` wheel, not the later MCP export/replay source candidate.

Preflight scope:

- [x] Version metadata aligned at `0.4.0` (`pyproject.toml`, `mneme.__version__`,
      README wheel URL, changelog).
- [x] Tests green on the release candidate source.
- [x] Wheel builds and installs in a clean environment; installed `mneme --version`
      reports `0.4.0`.
- [x] Installed CLI smoke covers `mneme bench`, real Gather `DocsSource` -> Mneme
      `origin-recheck` (`MATCH`, then `DRIFT` after a fixture edit), and the
      existing Crucible replay boundary tests.
- [x] Secret scan over the release-prep diff is clean.

Release boundary:

- Local origin freshness is explicit and profile-bound. It compares Gather's
  normalized decoded text for `docs` / `file-read`; it does not prove raw bytes
  and does not fetch network origins.
- Crucible replay remains assessment-bound replay of Mneme measurements from a
  caller-owned SQLite snapshot. It does not become external source
  certification.
- This checklist does not claim full enterprise completeness.

Publication notes:

- `.github/workflows/release.yml` builds, verifies version/tag alignment,
  installs the wheel, smokes `mneme --version` and `mneme bench`, and uploads
  `dist/` as a GitHub Actions artifact. It does not attach the wheel or sdist
  to a GitHub Release page.
- For any future GitHub Release, first update project metadata to an unpublished
  release version, then attach checked wheel/sdist assets explicitly and verify
  their hashes against the release-build receipt.

## 0.1.0 historical delivery note

Everything below is verified. Publishing is the operator's call (outward-facing,
irreversible); this turns it into a reviewed, pre-flighted, four-command action.

## Preflight (verified 2026-07-07)

- [x] **Tests green** - 42 falsifiers pass (`python -m pytest -q`).
- [x] **Zero runtime dependencies** - stdlib only; `pytest` is the sole dev dep.
- [x] **No secrets** - credential scan of `src/`+`tests/` clean; no `.env`, `.db`,
      `.key`, or `.token` tracked (`.gitignore` covers them).
- [x] **Clean working tree** - no uncommitted changes.
- [x] **Wheel builds and runs** - `mneme_memory-0.1.0-py3-none-any.whl` built,
      installed in a fresh venv with no `src` on path; `mneme --version`,
      `mneme bench` (76.6% / 100% recall), and the documented MCP tools resolve from the
      installed package.
- [x] **LICENSE** and **CHANGELOG** present; `pyproject.toml` metadata
      complete (name `mneme-memory`, console script `mneme`, urls).
- [x] **CI written** - `.github/workflows/ci.yml`: pytest on ubuntu/windows/macos
      x py3.11-3.13 + a wheel-install job.

## Status

- [x] **GitHub: LIVE** - https://github.com/HarperZ9/mneme (public, pushed 2026-07-07).
- [ ] **PyPI** - one step away, tokenless via OIDC. Do this once on PyPI, then tag:

```bash
# one-time on PyPI: add a trusted publisher
#   project: mneme-memory ; owner: HarperZ9 ; repo: mneme ; workflow: release.yml
# after project.version names the unpublished release you intend:
PKG_VER=$(python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
git tag "v$PKG_VER" && git push origin "v$PKG_VER"
```

The `release.yml` workflow builds, verifies the tag matches the version,
installs the wheel and smokes the CLI, then publishes via OIDC. No token passes
through any tool but PyPI's own trusted-publisher handshake.

## Positioning (for the release notes)

Category: agent long-term memory (vs Mem0, TencentDB-Agent-Memory, Zep). mneme
matches the class's surface - 4-tier L0-L3, hybrid BM25+vector retrieval, MCP,
memory edit/delete - and adds what none of them ship: provenance on every
memory, a re-derivable recall receipt, self-flagging drift, auditable forgetting,
a re-derivable token benchmark (76.6% reduction at 100% answer-recall, vs the
category's 61% headline that does not prove the answer survived), and the
ecosystem moat - a recalled memory that traces back to its origin receipt,
with supported local origin freshness checks kept separate from internal drift.
Zero-dep, deterministic; see [LICENSE](LICENSE).
