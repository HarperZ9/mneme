# Usage

Mneme records memories with provenance, returns recall receipts that can be re-derived, and checks whether cited local sources still match the stored measurement.

## Install

The README names the current release and its install commands. Version 0.5.0 adds the changes described below: a forget that erases source turns and derived rows, `mneme status` and `mneme doctor`. To work from a source checkout:

```bash
python -m pip install -e .
```

For development with tests from source:

```bash
python -m pip install -e ".[test]"
```

## Configure state

The CLI takes `--state` before the command; if omitted, Mneme uses `mneme.db` in the current working directory, so a command run in another directory uses another database. The MCP server reads `MNEME_STATE` with the same default, and its Crucible export/replay tools refuse to run when the binding is unset or empty. Use synthetic SQLite state for examples and interop checks.

Two commands show where the state is:

```bash
mneme --state mneme.db status    # absolute path, default or not, files, row counts, snapshot directory
mneme --state mneme.db doctor    # the same plus the audit chain; exit 1 on any warning
```

Both open the database read-only and never create it. They warn when the database sits inside a git work tree, when an older mneme reopened it, and when replay snapshots were left behind. Use one mneme version per database file.

Replay snapshots are full copies of the database. They live in `<LocalAppData>/mneme/snapshots` on Windows and in `$XDG_STATE_HOME/mneme/snapshots` (or `~/.local/state/mneme/snapshots`) elsewhere, never beside the database.

## Common CLI commands

```bash
mneme --state mneme.db remember chat session.json --user alice
mneme --state mneme.db recall "where does the user live" --user alice --json
mneme --state mneme.db drift
mneme --state mneme.db provenance <memory_id>
mneme --state mneme.db audit
mneme bench
```

## Forget

`forget` erases a memory, the turns it came from, and everything derived from them: memories that cite an erased turn or memory, scenario and persona rows, and the fact's supersession history.

```bash
mneme --state mneme.db forget <memory_id> --dry-run            # the plan, nothing deleted
mneme --state mneme.db forget <memory_id> --reason "user asked" # shows the plan, then asks
mneme --state mneme.db forget t1 --turn --yes                   # a turn and what derives from it
mneme --state mneme.db forget chat --session --yes              # a whole session
```

Memories that only share a source turn with the target are collateral, and rows of the same user that repeat an erased text whole are duplicates. The command lists both and asks; with `--yes` it refuses them until `--allow-collateral` is given. `--keep-sources` keeps the source turns. The plan shows row text only on a terminal or with `--show-text`, since a plan printed to a pipe can reach an agent's model, and `--dry-run` opens the database read-only. The reason is stored verbatim in the audit log, so a reason that repeats erased text, or names an erased row's id or content hash, is refused; the check catches verbatim repeats only. Run `--emit-opening` yourself: it prints the commitment salts to whoever reads stdout.

The receipt's `status` is `erased` only when every check passed; `findings` lists the rest. `erased_residue_found` means the store still holds erased text (another user's row, an earlier audit reason that quoted it, or the source of a near-duplicate an older mneme merged away without a link). `erased_copies_remain` means a replay snapshot outside this store's directory, or one removed while its process ran, holds erased text. `incomplete` means a store file or a known copy could not be read. `erased_sources_kept` follows `--keep-sources`. `erased_unverified` means the rows are gone but the scrub or scan did not finish; status and doctor warn until `mneme scrub` finishes it. The command exits 3 for every status except `erased` and `erased_sources_kept`.

The receipt also names what the erase cannot remove: audit rows written before the erase still name erased rows by content-derived id, which can confirm a guessed text when its source turn id is known (their update and supersede values are salted commitments from schema 5, and the erase deletes the salts; rows from before schema 5 keep plain hashes and are counted), freed disk blocks and deleted snapshots can hold old bytes until reused, and exports, backups, other copies of the database file and text already sent to a model provider are out of reach. `mneme scrub` removes the store's replay snapshots and finishes a scrub that another open connection blocked or an interrupt stopped.

A forget in 0.4.2 and earlier left the raw turn in the store. `mneme status` counts those forget entries and the turns no memory cites; `mneme forget <turn_id> --turn` erases such a turn.

## MCP

```bash
MNEME_STATE=mneme.db mneme mcp
```

The server exposes the memory, recall, drift, provenance, origin recheck, forget, audit, status, doctor, Crucible export, and Crucible replay tools. `mneme.to_crucible` requires either a specific `user` selector or `all_users: true`; session filters must be non-empty strings, and layer filters are limited to `L1`, `L2`, or `L3`.

From 0.5.0, `mneme.forget` takes two steps: a call with only `memory_id` returns the plan (targets, row ids and counts, the number of users but not their names) and deletes nothing, and a second call with `confirm_plan_sha256` applies exactly that plan. When the plan lists collateral or duplicate rows, the second call also needs `allow_collateral: true`. The model that asked for the plan can send the digest too, so a host that launches mneme for an agent should require an owner-granted step for this tool. `mneme.doctor` returns the absolute database path (home written as `~`), the replay snapshot directory and any warnings without opening the database.

## Crucible replay

Mneme exports `mneme.crucible-export/2` for Crucible assessment. After Crucible produces a `crucible.replay-template/1`, replay it against a caller-owned SQLite snapshot and write a new replay pack:

```bash
mneme --state mneme-replay-snapshot.db replay-crucible replay-template.json --out replay-pack.json
```

Descriptors remain declarative: no executable commands, host state paths, or database paths belong inside them. All-row templates are checked against the assessment measurement seal. Templates with skipped rows are refused until the contract includes a verifier-enforced full denominator for disclosed, skipped, and undisclosed rows.

## Limits

Origin rechecks are opt-in and profile-bound. Supported Gather docs or file-read receipts compare normalized decoded text; Mneme does not fetch network origins or prove raw-byte integrity for those receipts. Crucible replay verifies Mneme's sealed measurements and does not independently read external sources. A forget reaches the database file and its replay snapshots, and scans other snapshots it can find; it does not reach exports, backups, copies of the file made elsewhere, or text already sent to a model provider through an LLM extractor, an embedder or an MCP client.
