# Mneme-to-Crucible Export Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task by task.

**Goal:** Make `AgentMemory.to_crucible()` emit measurements that Crucible can load directly while preserving MATCH, DRIFT, and fail-closed UNVERIFIABLE semantics.

**Architecture:** Keep Mneme decoupled and zero-dependency. Emit Crucible's documented JSON carrier shape from the existing composition module, then use an optional sibling-checkout integration test to exercise Crucible's real loader and assessor. Correct prose that currently overstates independent source certification.

**Tech Stack:** Python 3.10+, stdlib JSON/SQLite, pytest, existing Mneme and Crucible APIs.

**Spec:** `C:/dev/project-docs/specs/SPEC-QCR-FALSIFIABLE-LANES-20260721.md`

---

### Task 1: Pin the real measurement contract with a failing test

**Files:**
- Modify: `tests/test_compose.py`

**Step 1: Replace assertions for the obsolete row shape**

Assert every fresh row uses:

```python
assert row["claim"]
assert row["deviation"] == 0.0
assert row["tolerance"] == 0.5
assert row["method"] == "mneme.drift/v1"
assert row["mneme_verdict"] == "MATCH"
```

Assert a drifted row uses `deviation == 1.0`. Add a fail-closed unit case by
monkeypatching `mneme.compose.check_memory` to return a `MemoryVerdict` with
`UNVERIFIABLE`, then assert `deviation is None`.

**Step 2: Make the round trip exercise measurements**

Serialize the whole export to `tmp_path / "export.json"`, load the thesis with
Crucible's `_thesis_from_data`, load the measurements with its real
`_load_measurements`, call `assess(thesis, measurements)`, and assert the fresh
verdicts are all `MATCH`. Keep this optional when Crucible is genuinely absent so
standalone Mneme remains zero-dependency; do not swallow assertion or loader
failures once imports succeed.

**Step 3: Run the focused test and observe RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest tests/test_compose.py -q -p no:cacheprovider
```

Expected: failure because version 1 rows have no `claim`, `deviation`, or
`method`, and the real loader resolves an empty claim.

### Task 2: Emit the honest Crucible carrier

**Files:**
- Modify: `src/mneme/compose.py`

**Step 1: Implement one explicit verdict mapping**

Use a small private pure helper or equivalent direct mapping:

```python
def _deviation(verdict: str) -> float | None:
    return 0.0 if verdict == MATCH else 1.0 if verdict == DRIFT else None
```

For each memory emit `claim`, `deviation`, `tolerance`, `method`, `evidence`, and
`mneme_verdict`. Do not emit `trusted=True`, and do not coerce UNVERIFIABLE to
DRIFT. Set the top-level schema to `mneme.crucible-export/2`.

**Step 2: Correct the evidence boundary**

State that Crucible independently recomputes the verdict from Mneme's drift
measurement. State explicitly that source re-reading remains Mneme-provided
unless an external recheck oracle is attached. Do not advertise this export as
independent source certification.

**Step 3: Run the focused test and observe GREEN**

Use the Task 1 command. Expected: all composition tests pass, including the real
loader when the sibling checkout is present.

### Task 3: Retire stale public wording

**Files:**
- Modify: `src/mneme/cli.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_compose.py`

**Step 1: Update user-facing text**

Replace "independent organ certifies faithfulness" language with the narrower
contract: Mneme supplies drift measurements; Crucible independently recomputes
and seals MATCH/DRIFT/UNVERIFIABLE. Mention schema v2 in the README. Keep the
historical changelog accurate rather than preserving a false capability claim.

**Step 2: Pin the text boundary in tests**

Assert schema v2 and that the note includes both `recompute` and a clear source
verification limitation.

**Step 3: Run focused tests**

Run the Task 1 command.

### Task 4: Regression, review, and publication gate

**Files:**
- Review all changed files above.

**Step 1: Run full Mneme verification**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest -q -p no:cacheprovider
```

**Step 2: Inspect scope and secrets**

```powershell
git status --short
git diff --check
git diff --stat
git diff
```

**Step 3: Request spec and quality review**

Resolve all blocking findings and rerun the exact focused and full gates after
the final edit.

**Step 4: Commit and publish only the isolated slice**

Commit message: `fix: make Mneme exports loadable by Crucible`. Push branch
`fix/qcr-crucible-export`, open a PR against the repository's current default
branch, and recheck GitHub Actions. If access or branch policy blocks publishing,
record the exact command/output without altering other worktrees.
