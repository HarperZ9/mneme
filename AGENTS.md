# AGENTS.md - mneme

This repository is self-contained. Mneme is a Python package for accountable agent memory with provenance, re-derivable recall receipts, drift checks, and Crucible replay interop.

## Working rules

- Read `README.md`, `DELIVERY.md`, and the relevant tests before release or interop changes.
- Preserve existing CLI, library, and MCP schemas unless the change is an explicit compatibility break with tests and docs.
- Keep state databases, real user fixtures, private receipts, credentials, `.env` files, tokens, and machine-local paths out of git.
- Use synthetic SQLite state for tests, examples, and MCP interop checks.
- Treat `MNEME_STATE` as the operator's MCP state binding. MCP export and replay paths should fail closed when it is unset or empty.
- Treat `user` as a selector inside the configured state, not as an authentication boundary.
- Keep Crucible descriptors declarative. Do not put executable commands, host state paths, or database paths in untrusted descriptors.
- Preserve fail-closed behavior for malformed replay templates, omitted rows, source drift controls, cleanup warnings, and duplicate JSON keys.

## Validation

For runtime changes, run focused regression tests first, then the relevant full suite before release preparation. For docs-only changes, run the public delivery-surface checks that apply. Do not tag, publish, or claim release availability until the checked candidate bytes and release action have explicit operator approval.
