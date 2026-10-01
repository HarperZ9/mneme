# Mneme local client packaging

## Objective
Ship an additional local client package alongside the Python product, with no publisher service or model account.

## Requirements
- Keep the current version until the parent release owner chooses a mature release.
- Bind Mneme state explicitly; bind Relay launch root explicitly with no ambient write or exec grants.
- Preserve Plexus discovery as declared evidence, not a runtime check.
- Build deterministic source ZIP and Windows binary MCPB/ZIP with checksums and native provenance.
- Refuse linked package inputs, dirty release sources, non-.0 release versions and mismatched tags.
- Test real stdio identity, safe requests and denied requests without real stores, keys or model calls.

## Release boundary
Development artifacts are not published releases or proof of marketplace/client acceptance.
No installer, service, network listener or scheduled process is registered.

## Status
Implemented and independently reviewed as a development candidate. Full source suites and restricted native stdio checks pass. Clean tagged rebuild, installed-client acceptance, Windows signing and marketplace admission remain open. Mneme granted native persistence and Relay model-driven workflows remain unverified.
