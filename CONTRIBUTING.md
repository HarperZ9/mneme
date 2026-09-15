# Contributing

Mneme is a Python package for accountable agent memory with provenance, re-derivable recall receipts, drift checks, and Crucible replay interop. Keep contributions small enough to review against those contracts.

## Development

```bash
python -m pip install -e ".[test]"
python -m pytest
```

Use focused regression tests for semantic changes, then run the relevant full suite before release preparation. For docs-only changes, run the public delivery-surface checks that apply to the changed files.

## Data and state

Use synthetic SQLite state for tests, examples, and MCP interop checks. Do not commit real user databases, local receipts, credentials, `.env` files, tokens, or private machine paths. `MNEME_STATE` is an operator binding for the MCP server, and `user` is a selector inside that configured state rather than an authentication boundary.

## Replay and descriptors

Crucible replay descriptors are declarative. They should not carry executable commands, host state paths, or database paths. Preserve fail-closed behavior for malformed templates, omitted rows, source drift controls, cleanup warnings, and duplicate JSON keys.

## Release boundary

Do not tag, publish, or claim release availability until the release gates in [DELIVERY.md](DELIVERY.md) have been checked on the candidate bytes and the operator has explicitly approved the release action.
