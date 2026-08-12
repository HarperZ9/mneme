# Tenant-Bound MCP Object Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent an MCP caller from reading or deleting a memory it does not own.

**Architecture:** Keep the guard at the MCP object-access boundary. `mneme.provenance` and `mneme.forget` require a non-empty `user`, `AgentMemory` passes that user to the store, and the store resolves the row with both memory id and user. A foreign row is not exposed to the caller and follows the same no-memory path as an absent row.

**Tech Stack:** Python 3.11+, stdlib SQLite, pytest, JSON-RPC/MCP.

## Global Constraints

- Change only `mneme.provenance` and `mneme.forget` MCP object access.
- Preserve existing owner behavior; return the generic `memory not found` MCP
  error for both absent and foreign-owned objects.
- Do not extend the guard to recall, audit, L0 turns, or userless direct-library APIs in this change.
- Use real `handle_request` MCP calls in the regression test, with no network or provider calls.

---

### Task 1: Guard MCP provenance and forget by owner

**Files:**
- Modify: `tests/test_scenario_mcp.py`
- Modify: `src/mneme/store.py`
- Modify: `src/mneme/memory.py`
- Modify: `src/mneme/mcp.py`

**Interfaces:**
- Consumes: `AgentMemory.provenance(memory_id)` and `AgentMemory.forget(memory_id, reason)`.
- Produces: `AgentMemory.provenance(memory_id, user)` and `AgentMemory.forget(memory_id, reason, user)`, using a store lookup constrained by `(id, user)`.

- [ ] **Step 1: Write the failing MCP regression test**

```python
def test_mcp_object_access_requires_the_owner(tmp_path, monkeypatch):
    # Seed distinct tenant-owned memories, then exercise only `handle_request`.
    # Assert a foreign read and delete produce the exact same error text as a
    # nonexistent id, preserve the foreign row, and permit the owner to read
    # and delete its own row.
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m pytest tests/test_scenario_mcp.py::test_mcp_object_access_requires_the_owner -v`

Expected: FAIL because `mneme.provenance` and `mneme.forget` currently accept missing `user` and find rows by id only.

- [ ] **Step 3: Write the minimal implementation**

```python
# mcp.py
_reject_unknown(args, {"memory_id", "user"})
user = str(args.get("user", ""))
if not user:
    raise ValueError("user is required")
prov = mem.provenance(str(args["memory_id"]), user=user)

# memory.py
def provenance(self, memory_id: str, *, user: str) -> dict | None:
    r = self.store.memory_for_user(memory_id, user)

# store.py
def memory_for_user(self, memory_id: str, user: str) -> sqlite3.Row | None:
    return self.conn.execute(
        'SELECT * FROM memories WHERE id=? AND "user"=?', (memory_id, user)
    ).fetchone()
```

- [ ] **Step 4: Run focused tests to verify the guard**

Run: `python -m pytest tests/test_scenario_mcp.py -v`

Expected: PASS, including the owner, foreign-owner, absent-id, and missing-user assertions.

- [ ] **Step 5: Run the full suite and review change hygiene**

Run: `python -m pytest`, then `git diff --check`, `git diff --check --cached`, and a tracked-file secret-pattern scan.

Expected: all tests pass; no whitespace errors; no added credential material.

- [ ] **Step 6: Commit after verification**

```bash
git add docs/superpowers/plans/2026-08-12-tenant-object-guard.md \
        tests/test_scenario_mcp.py src/mneme/store.py src/mneme/memory.py src/mneme/mcp.py
git commit -m "fix: guard MCP memory objects by tenant"
```

## Self-Review

- Spec coverage: owner success, foreign read/delete denial, target survival, missing-user rejection, indistinguishable absent/foreign errors, and residual unscoped surfaces are each covered by Task 1.
- Placeholder scan: no incomplete work markers or deferred implementation steps remain.
- Type consistency: MCP uses keyword-only `user`; the facade delegates to a store lookup constrained by id and user; forget uses the same owner-bound lookup before auditing and deletion.

## Residual Scope

This guard does not make Mneme fully multi-tenant. In particular, MCP recall remains optionally user-scoped, audit is global, L0 turns lack a user column, and direct library/store callers can still use userless APIs. Those surfaces require an explicit, broader tenancy design.
