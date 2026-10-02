# Privacy

Mneme runs on the user's computer. The publisher operates no backend for this package.
The connected client and its model can see tool arguments and results under that client's terms.
No model is included. Any model endpoint configured by the user belongs to that user.
Local access is granted to the running process by the operating system; a client permission dialog is not an OS sandbox.
Do not connect private stores or directories to an untrusted client. Stop the client process to disconnect.
Snapshot writes and cleanup use a state-specific sibling directory. Legacy global copies remain outside this client's authority and are reported as unchecked by forget receipts.

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

**Network.** The server opens no network connection. Its code opens no socket, makes no web request and starts no other program. It talks to Claude Code only through standard input and output. Mneme's library has an optional model-based extractor that can call a model endpoint. The plugin server never loads it.

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

Nothing else is read. `XDG_STATE_HOME` appears in Mneme's snapshot code, but only the command-line tool on Linux and macOS uses it. The plugin server keeps snapshots beside your database and never reads it. The server reads no credential, API key or token. The optional model extractor reads `OPENAI_BASE_URL`, `OPENAI_MODEL` and `OPENAI_API_KEY`, and the plugin server never loads it.

## What it reads, stores and sends

Mneme reads and, with the memory-write grant, writes the SQLite file you select at
install. Origin recheck reads the cited source files inside the folder named in
that call. Replay snapshots go in a folder beside the SQLite file. Mneme opens no network
connection and collects no usage statistics.

## Retention and support

Memories stay in your SQLite file until you remove them with `mneme.forget` or delete
the file. Support and security reports: https://github.com/HarperZ9/mneme/issues
