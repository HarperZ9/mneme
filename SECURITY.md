# Security Policy

## Supported versions

`mneme-memory` is released from this repository. Security fixes are applied to
the current release line and published as a new patch release. Older lines are
not backported.

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |
| < 0.1   | No        |

## Reporting a vulnerability

Please report suspected vulnerabilities privately. Do not open a public issue,
pull request, or discussion for a security report.

Send the report to `<SECURITY CONTACT>`. Include:

- the affected version and platform,
- a description of the issue and its impact,
- the minimal steps or input needed to reproduce it.

You can expect an acknowledgement, and we will work a fix before any public
disclosure. Please allow a reasonable period to remediate before disclosing.

## Scope and trust model

`mneme-memory` is an accountable agent-memory library. It stores memories in a
local database that you own and control. The core is zero-dependency.

- Data at rest: memory contents live in a local database file. That file can hold
  whatever your agent chose to remember, so treat it as sensitive. The shipped
  `.gitignore` excludes `.env`, key and token files, and `*.db` so a memory
  database is not committed by accident. Keep it on storage you control.
- No network by default: the core performs no network calls and stores no
  credentials. If you inject an embedder that calls a remote endpoint, that edge
  and its credentials are your responsibility, and its key should come from the
  environment.
- MCP surface: mneme ships a stdio MCP server. It speaks JSON-RPC over stdio and
  does not open a network port on its own.

## Good practice

- Keep any embedder or backend credentials in the environment, not in code.
- Store the memory database on encrypted, access-controlled storage when it holds
  sensitive content.
