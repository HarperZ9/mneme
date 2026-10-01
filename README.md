## Marketplace source distribution

This folder packages the source plugin from release 0.6.0. It requires Python 3.11 or later, available as `python3`. It includes the tool source and no model or bundled runtime. The connected client supplies any model used in the conversation.

The separate [Windows x64 native download](https://github.com/HarperZ9/mneme/releases/download/v0.6.0/mneme-0.6.0-win-x64.mcpb) includes its runtime. That download is a manual MCPB package and is not part of this source plugin. Directory approval and availability remain unverified.

This branch contains the installable plugin. Build commands in the release README below apply to the [product source tag](https://github.com/HarperZ9/mneme/tree/v0.6.0). DISTRIBUTION.json records the published asset digest and every packaging change; any SOURCE.json describes the original release payload.

# Mneme client package

Claude Code asks for the required SQLite file path when it enables this plugin. For portable or generic MCP clients, set MNEME_STATE to an absolute SQLite file path whose parent exists. No working-directory fallback is accepted. The default adapter offers status, doctor, local origin recheck and declarative Crucible export. Add --allow-memory-write only after approving remember, recall, drift, provenance, audit, replay and two-step forget access: legacy reads can initialize/migrate state, and replay/forget manage local snapshots. The user selector is not authentication.

Client snapshots stay in a `.mneme-snapshots-<state-path-hash>` directory beside the selected database. The client neither inspects nor cleans the legacy global snapshot directories or shared temporary snapshots. Forget receipts retain `copies_unchecked` for those locations even after the selected memory is removed. CLI snapshot defaults are unchanged; review legacy copies through that workflow separately. Path checks refuse links but do not provide an OS sandbox or protect against concurrent filesystem changes by another process.

## Install
The source ZIP requires Python 3.11 or newer. Extract the entire archive, then point a local stdio MCP client at an absolute Python executable with arguments `-I -S -B server/serve.py` using the absolute script path. Set the binding above in the client environment. The source package is an advanced installation, not self-contained.

The Windows x64 native ZIP includes Python and needs no separate Python or Node installation. Extract everything and use the absolute `server/mneme-local.exe` path with no arguments. A client supporting binary MCPB extensions may open the matching MCPB; enter its required local binding. Both archives use identical executable bytes.

MCPB setup includes **Allow memory changes**, off by default. Enable it to grant memory storage, recall, replay and two-step forgetting for the selected database. The host passes `--memory-write=true` or `--memory-write=false`; missing, malformed or unresolved values cannot enable the grant. Manual clients can still use `--allow-memory-write`. Restart the connection after changing setup.

Portable plugin.json/mcp.json, Claude's .claude-plugin/plugin.json and .mcp.json, and Codex's .codex-plugin/plugin.json are generated from the same source version. The source manifests use python3; replace that command with an absolute trusted Python path if unavailable. No client configuration is modified automatically. ChatGPT or Claude cloud support and marketplace admission are not implied by local MCP compatibility.

## Troubleshooting
An absent binding, relative path, linked path component or unknown launch argument stops startup. Correct the explicit path and restart the client. After a permission refusal, the operator must decide whether to change the launch grants. Check SHA256SUMS before extracting and keep the full package together. Unsigned Windows binaries can trigger platform warnings; Code signing and clean-machine acceptance are not established by this source distribution.

The tool source matches the published source-plugin asset; DISTRIBUTION.json records the packaging changes in this branch. No background service, network listener, cloud account or publisher compute is created.

## Claude Code state file

Select an absolute SQLite file path whose parent folder exists. The file can be new. No default state path is supplied, and memory changes remain disabled. Portable MCP clients still use `MNEME_STATE` as described above. Cowork setup for this required setting is not established; use Claude Code for this source distribution.

## Optional memory grant

In Claude Code setup, **Allow memory changes** defaults to false. Enable it only when you approve storage, recall, drift, provenance, audit, replay and two-step forgetting for the selected database. Some reads can initialize or migrate state; replay and forgetting can manage local snapshots. Forgetting requires a plan followed by explicit confirmation. Restart the connection after changing this setting. The state path alone grants none of these operations.
