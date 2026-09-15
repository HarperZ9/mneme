# Usage

Mneme records memories with provenance, returns recall receipts that can be re-derived, and checks whether cited local sources still match the stored measurement.

## Install

The public `v0.4.0` wheel is the current released package. It covers the released memory, recall, drift, provenance, accountable forgetting, and local-origin freshness workflows. It does not include the source-candidate MCP Crucible export/replay tools.

```bash
python -m pip install "https://github.com/HarperZ9/mneme/releases/download/v0.4.0/mneme_memory-0.4.0-py3-none-any.whl"
```

Use a source checkout for the MCP Crucible export/replay candidate until a matching future release asset exists:

```bash
python -m pip install -e .
```

For development with tests from source:

```bash
python -m pip install -e ".[test]"
```

## Configure state

Most CLI commands accept `--state`; if omitted, Mneme uses `mneme.db` in the current working directory. The source-candidate MCP export/replay tools read `MNEME_STATE` and refuse that work when the binding is unset or empty. Use synthetic SQLite state for examples and interop checks.

## Common CLI commands

```bash
mneme --state mneme.db remember chat session.json --user alice
mneme --state mneme.db recall "where does the user live" --user alice --json
mneme --state mneme.db drift
mneme --state mneme.db provenance <memory_id>
mneme --state mneme.db audit
mneme bench
```

## MCP

```bash
MNEME_STATE=mneme.db mneme mcp
```

The released `v0.4.0` wheel exposes the released MCP memory, recall, drift, provenance, origin recheck, forget, audit, status, and doctor tools. The source candidate adds MCP Crucible export/replay through `mneme.to_crucible` and `mneme.replay_crucible`. `mneme.to_crucible` requires either a specific `user` selector or `all_users: true`; session filters must be non-empty strings, and layer filters are limited to `L1`, `L2`, or `L3`.

## Crucible replay

From the source candidate, Mneme exports `mneme.crucible-export/2` for Crucible assessment. After Crucible produces a `crucible.replay-template/1`, replay it against a caller-owned SQLite snapshot and write a new replay pack:

```bash
mneme --state mneme-replay-snapshot.db replay-crucible replay-template.json --out replay-pack.json
```

Descriptors remain declarative: no executable commands, host state paths, or database paths belong inside them. All-row templates are checked against the assessment measurement seal. Templates with skipped rows are refused until the contract includes a verifier-enforced full denominator for disclosed, skipped, and undisclosed rows.

## Limits

Origin rechecks are opt-in and profile-bound. Supported Gather docs or file-read receipts compare normalized decoded text; Mneme does not fetch network origins or prove raw-byte integrity for those receipts. Crucible replay verifies Mneme's sealed measurements and does not independently read external sources.
