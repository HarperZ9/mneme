# Mneme client package

Mneme gives your assistant a local memory in a SQLite file you choose. Every recalled memory carries its provenance, and memory changes stay off until you allow them.

## Try it

- Check Mneme's status and which database it is bound to.
- Export the memories from my planning session as Crucible claims.
- Re-check whether that memory still matches its source file.

## Details

Set MNEME_STATE to an absolute SQLite file path whose parent exists. No working-directory fallback is accepted. The default adapter offers status, doctor, local origin recheck and declarative Crucible export. Add --allow-memory-write only after approving remember, recall, drift, provenance, audit, replay and two-step forget access: legacy reads can initialize/migrate state, and replay/forget manage local snapshots. The user selector is not authentication.

Client snapshots stay in a `.mneme-snapshots-<state-path-hash>` directory beside the selected database. The client neither inspects nor cleans the legacy global snapshot directories or shared temporary snapshots. Forget receipts retain `copies_unchecked` for those locations even after the selected memory is removed. CLI snapshot defaults are unchanged; review legacy copies through that workflow separately. Path checks refuse links but do not provide an OS sandbox or protect against concurrent filesystem changes by another process.

## Install
The source ZIP requires Python 3.11 or newer. Extract the entire archive, then point a local stdio MCP client at an absolute Python executable with arguments `-I -S -B server/serve.py` using the absolute script path. Set the binding above in the client environment. The source package is an advanced installation, not self-contained.

The Windows x64 native ZIP includes Python and needs no separate Python or Node installation. Extract everything and use the absolute `server/mneme-local.exe` path with no arguments. A client supporting binary MCPB extensions may open the matching MCPB; enter its required local binding. Both archives use identical executable bytes.

MCPB setup includes **Allow memory changes**, off by default. Enable it to grant memory storage, recall, replay and two-step forgetting for the selected database. The host passes `--memory-write=true` or `--memory-write=false`; missing, malformed or unresolved values cannot enable the grant. Manual clients can still use `--allow-memory-write`. Restart the connection after changing setup.

Portable plugin.json/mcp.json, Claude's .claude-plugin/plugin.json and .mcp.json, and Codex's .codex-plugin/plugin.json are generated from the same source version. The source manifests use python3; replace that command with an absolute trusted Python path if unavailable. No client configuration is modified automatically. ChatGPT or Claude cloud support and marketplace admission are not implied by local MCP compatibility.

## Troubleshooting
An absent binding, relative path, linked path component or unknown launch argument stops startup. Correct the explicit path and restart the client. After a permission refusal, the operator must decide whether to change the launch grants. Check SHA256SUMS before extracting and keep the full package together. Unsigned Windows binaries can trigger platform warnings; signing and clean-machine/client acceptance remain release gates.

These development bytes may carry the current source version but are not the existing published release. No background service, network listener, cloud account or publisher compute is created.
