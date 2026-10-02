# Mneme client package

Mneme gives your assistant a local memory in a SQLite file you choose. Every recalled memory carries its provenance, and memory changes stay off until you allow them.

## Try it

- Check Mneme's status and which database it is bound to.
- Export the memories from my planning session as Crucible claims.
- Re-check whether that memory still matches its source file.

## Details

In Claude Code, enabling the plugin asks for the **Mneme state database**, an absolute SQLite file path whose parent folder exists, and for **Allow memory changes**, which defaults to off. The Claude manifest passes the path as `MNEME_STATE` and the switch as `--memory-write`. Other clients set MNEME_STATE to an absolute SQLite file path whose parent exists. No working-directory fallback is accepted. The default adapter offers status, doctor, local origin recheck and declarative Crucible export. Add --allow-memory-write only after approving remember, recall, drift, provenance, audit, replay and two-step forget access: legacy reads can initialize/migrate state, and replay/forget manage local snapshots. The user selector is not authentication.

Client snapshots stay in a `.mneme-snapshots-<state-path-hash>` directory beside the selected database. The client neither inspects nor cleans the legacy global snapshot directories or shared temporary snapshots. Forget receipts retain `copies_unchecked` for those locations even after the selected memory is removed. CLI snapshot defaults are unchanged; review legacy copies through that workflow separately. Path checks refuse links but do not provide an OS sandbox or protect against concurrent filesystem changes by another process.

## Install
The plugin folder carries its own copy of the server code under `server/src`, limited to the modules the server can import, so Claude Code needs only that folder and Python 3.11 or newer. If that copy is missing, the server stops with a message asking you to reinstall the plugin. The source ZIP holds the same files. Extract the entire archive, then point a local stdio MCP client at an absolute Python executable with arguments `-I -S -B server/serve.py` using the absolute script path. Set the binding above in the client environment. The source package is an advanced installation, not self-contained.

The Windows x64 native ZIP includes Python and needs no separate Python or Node installation. Extract everything and use the absolute `server/mneme-local.exe` path with no arguments. A client supporting binary MCPB extensions may open the matching MCPB; enter its required local binding. Both archives use identical executable bytes.

MCPB setup includes **Allow memory changes**, off by default. Enable it to grant memory storage, recall, replay and two-step forgetting for the selected database. The host passes `--memory-write=true` or `--memory-write=false`; missing, malformed or unresolved values cannot enable the grant. Manual clients can still use `--allow-memory-write`. Restart the connection after changing setup.

Portable plugin.json/mcp.json, Claude's .claude-plugin/plugin.json and .mcp.json, and Codex's .codex-plugin/plugin.json are generated from the same source version. Claude's manifests take the state path and write switch from `${user_config.*}` values; portable and Codex manifests keep the `${MNEME_STATE}` placeholder, which the client must resolve. The source manifests use python3; replace that command with an absolute trusted Python path if unavailable. No client configuration is modified automatically. ChatGPT or Claude cloud support and marketplace admission are not implied by local MCP compatibility.

## What this plugin runs and handles

**Hooks.** This plugin has no hooks.

**MCP server.** The plugin starts one local server named `mneme`. Claude Code runs this command:

`python3 -I -S -B ${CLAUDE_PLUGIN_ROOT}/server/serve.py --memory-write=${user_config.memory_write}`

It also sets the environment variable `MNEME_STATE` to `${user_config.state_path}`.

- `${CLAUDE_PLUGIN_ROOT}` is the folder where Claude Code installed the plugin.
- `${user_config.state_path}` is the **Mneme state database** setting. You enter it when you enable the plugin. It must be an absolute path to a file in a folder that already exists.
- `${user_config.memory_write}` is the **Allow memory changes** setting. It is `false` unless you turn it on.
- With `false`, the server offers four tools: status, doctor, origin recheck and Crucible export. None of them changes the database.
- With `true`, it also offers remember, recall, drift, provenance, audit, replay and forget.
- `-I -S -B` start Python in isolated mode. Python then ignores its own environment variables and the user's site packages, and writes no bytecode cache files.

**Network.** The server opens no network connection. Its code opens no socket, makes no web request and starts no other program. It talks to Claude Code only through standard input and output. Mneme's library has an optional model-based extractor that can call a model endpoint. It is not shipped in this plugin folder.

**Files read.**

- The state database you chose, and its SQLite sidecar files.
- For `mneme.origin_recheck`, each local source file that a stored memory cites, inside the folder named in that call. A memory cites one file for each source turn that came from a local document. The folder comes from the tool call, so it can be any folder your account can read. Recheck reads each file to compare its hash and returns the result, not the file.
- For `mneme.doctor`, whether each folder above the database contains a `.git` entry, to warn you when the database sits inside a Git work tree.

**Files written.**

- The state database, at the path you chose, only when memory changes are allowed. While SQLite writes, it can keep a short-lived file beside it whose name ends in `-journal`. The database stays until you forget memories with the forget tool or delete the file.
- Replay snapshots, in a folder beside the database named `.mneme-snapshots-` plus a hash of the database path. A replay copies the database there and removes the copy when it ends. A copy left by a stopped process is removed at the next server start with memory changes allowed, or at the next replay. Forget also removes this database's snapshots.
- Nothing else. The server writes nothing to your home folder, the temporary folder or the plugin folder.

**Environment variables and credentials.**

These names come from running every tool, with memory changes off and on, under a recorder of the environment reads Python code makes. Reads inside compiled libraries such as SQLite are outside what the recorder sees.

Mneme's own code reads one variable:

- `MNEME_STATE` holds the state database path. Claude Code sets it from your setting. The server stops at startup if it is missing, is not absolute, or passes through a linked folder.

Python's standard library reads these on Mneme's behalf:

- `COLUMNS` and `LINES`: the argument parser reads them to size help text while it reads the launch arguments.
- `LANG`, `LANGUAGE`, `LC_ALL` and `LC_MESSAGES`: the argument parser reads them to choose a language for its messages.
- `USERPROFILE` on Windows, or `HOMEPATH` when `USERPROFILE` is not set: the doctor tool finds your home folder so it can show it as `~` in the paths it reports. On Linux and macOS the same step reads `HOME`.
- `APPDATA`, `PYTHONUSERBASE` and `_PYTHON_PROJECT_BASE`: on Windows, the doctor tool also asks Windows for the short form of your home folder path through Python's `ctypes` library. In Python 3.13.14, loading `ctypes` loads Python's build settings module, and that module reads these three. Python 3.12.10 and 3.13.2 do not read them.

Nothing else is read. `XDG_STATE_HOME` appears in Mneme's snapshot code, but only the command-line tool on Linux and macOS uses it. The plugin server keeps snapshots beside your database and never reads it. The server reads no credential, API key or token. The plugin folder carries only the modules the server can import, so the command-line tool and the optional model extractor are not shipped in it.

## Data and network

| Question | Answer |
| --- | --- |
| What it reads | The SQLite file you select, and for origin recheck the cited source files inside the folder you name in that call |
| What it stores | Conversation turns, extracted memories, provenance and an audit log in that SQLite file, only with memory changes allowed. Replay snapshots go in a folder beside it |
| Network calls | None. The client server opens no socket, makes no HTTP request and runs no other program |
| Telemetry | None |
| Retention | Memories stay in your SQLite file until you forget them or delete the file. Replay snapshots are removed when the replay closes, and a failed removal is reported |

Tool results go to the connected client, and that client's model provider handles them under its own privacy policy. See [PRIVACY.md](PRIVACY.md).

## Troubleshooting
An absent binding, relative path, linked path component or unknown launch argument stops startup. Correct the explicit path and restart the client. After a permission refusal, the operator must decide whether to change the launch grants. Check SHA256SUMS before extracting and keep the full package together. Unsigned Windows binaries can trigger platform warnings; signing and clean-machine/client acceptance remain release gates.

These development bytes may carry the current source version but are not the existing published release. No background service, network listener, cloud account or publisher compute is created.
