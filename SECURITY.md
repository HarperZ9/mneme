# Security Policy

## Supported versions

Mneme is published from this repository as the `flywheel-mneme` package.
Security fixes go to the current release line and ship as a new release. Older
lines get no backports.

| Version | Supported |
|---------|-----------|
| 0.7.x   | Yes       |
| < 0.7   | No        |

## Reporting a vulnerability

Report suspected vulnerabilities privately through GitHub: open this
repository's **Security** tab and choose **Report a vulnerability**, or go to
<https://github.com/HarperZ9/mneme/security/advisories/new>. Please keep
security reports out of public issues, pull requests and discussions.

Include:

- the affected version and platform,
- a description of the issue and its impact,
- the minimal steps or input needed to reproduce it.

The maintainer, Zain Dana Harper, acknowledges the report and works on a fix
before any public disclosure. Please allow a reasonable period to remediate
before disclosing.

## Scope and trust model

Mneme is a memory library for AI agents. It stores memories in a local SQLite
database that you own and control. The core needs only the Python standard
library at run time.

- Data at rest: memory contents live in a local database file. That file can
  hold whatever your agent chose to remember, so treat it as sensitive. This
  repository's `.gitignore` excludes `.env` files, key and token files, and
  `*.db`, so a memory database is hard to commit by accident. Keep the database
  on storage you control.
- No network by default: the core makes no network calls and stores no
  credentials. Two optional edges send text off the machine. The model
  extractor (`LLMExtractor`) posts turns to an OpenAI-compatible endpoint and
  reads its key from an environment variable, `OPENAI_API_KEY` by default. An
  embedder you inject may call a remote endpoint. Those edges and their
  credentials are your responsibility.
- MCP surface: `mneme mcp` is a stdio server. It speaks JSON-RPC over stdio,
  opens no network port, and reads the database path from `MNEME_STATE`.
  Results an MCP client reads go to that client and to its model provider.
- `user` selects rows inside the configured database. It is a selector and
  gives no authentication boundary between users of one file.

Reports that matter most: a recall, drift, forget or audit receipt that
verifies after tampering, a forget that reports rows erased while they remain
readable through Mneme, an MCP call that reads or writes outside the bound
state, and a Crucible descriptor that causes a command to run.

## Good practice

- Keep any extractor, embedder or backend credentials in the environment and
  out of code.
- Store the memory database on encrypted, access-controlled storage when it
  holds sensitive content.
- Use one Mneme version per database file.
