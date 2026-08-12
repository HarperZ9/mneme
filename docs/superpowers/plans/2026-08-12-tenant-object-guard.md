# Tenant-Bound MCP Object Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent an MCP caller from reading or deleting a memory outside the
tenant configured for the MCP server.

**Architecture:** Keep the guard at the MCP object-access boundary.
`mneme.provenance` and `mneme.forget` derive their tenant from the non-empty
`MNEME_TENANT` server configuration. Their schemas do not accept `user`; a
caller-supplied value is rejected. `AgentMemory` passes the configured tenant to
the store, which resolves the row with both memory id and user. A foreign row is
not exposed to the caller and follows the same no-memory path as an absent row.

**Tech Stack:** Python 3.11+, stdlib SQLite, pytest, JSON-RPC/MCP.

## Global Constraints

- Change only `mneme.provenance` and `mneme.forget` MCP object access.
- Require explicit, non-empty `MNEME_TENANT` before object read or delete.
- Preserve configured-tenant behavior; return the generic `memory not found` MCP
  error for both absent and foreign-owned objects.
- Do not extend the guard to recall, audit, L0 turns, or userless direct-library APIs in this change.
- Use real `handle_request` MCP calls in the regression test, with no network or provider calls.

---

### Task 1: Guard MCP provenance and forget by configured tenant

**Files:**
- Modify: `tests/test_scenario_mcp.py`
- Modify: `src/mneme/store.py`
- Modify: `src/mneme/memory.py`
- Modify: `src/mneme/mcp.py`

**Interfaces:**
- Consumes: `AgentMemory.provenance(memory_id)` and `AgentMemory.forget(memory_id, reason)`.
- Produces: configured-tenant calls to `AgentMemory.provenance(memory_id, user)` and `AgentMemory.forget(memory_id, reason, user)`, using a store lookup constrained by `(id, user)`.

- [ ] **Step 1: Write the failing MCP regression test**

```python
def test_mcp_object_access_uses_the_configured_tenant(tmp_path, monkeypatch):
    # Configure the server as tenant B, then exercise only `handle_request`.
    # Assert foreign read/delete and tenant-A spoofing fail, a no-user forget
    # succeeds for tenant B, and a missing server tenant fails closed.
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m pytest tests/test_scenario_mcp.py::test_mcp_object_access_uses_the_configured_tenant -v`

Expected: FAIL because `mneme.provenance` and `mneme.forget` currently use the
caller-provided `user` instead of a configured server tenant.

- [ ] **Step 3: Write the minimal implementation**

```python
# mcp.py
_reject_unknown(args, {"memory_id"})
tenant = _configured_tenant()
prov = mem.provenance(str(args["memory_id"]), user=tenant)

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

- Spec coverage: configured-tenant success, foreign read/delete denial, spoofed
  tenant rejection, target survival, missing configured-tenant rejection,
  indistinguishable absent/foreign errors, and residual unscoped surfaces are
  each covered by Task 1.
- Placeholder scan: no incomplete work markers or deferred implementation steps remain.
- Type consistency: MCP obtains tenant identity only from `MNEME_TENANT`; the
  facade delegates to a store lookup constrained by id and user; forget uses
  the same owner-bound lookup before auditing and deletion.

## Residual Scope

This guard does not make Mneme fully multi-tenant. In particular, MCP recall remains optionally user-scoped, audit is global, L0 turns lack a user column, and direct library/store callers can still use userless APIs. Those surfaces require an explicit, broader tenancy design.
