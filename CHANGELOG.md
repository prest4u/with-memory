# Changelog

## Unreleased — production hardening

- Schema v2: stable library/device/object UIDs, structured scopes, principals/grants, sources/scans, separate candidates, supersession DAGs, and append-only redacted events while preserving integer fact IDs
- Explicit SQLite open modes, atomic write transactions, side-by-side migration, verified backups/restore/rollback, opportunity-based rotation, and interactive purge
- Candidate-first harness policy, source consent/revocation, pre-persistence content controls, idempotent submissions, local interactive review, and ACL-filtered MCP discovery
- Versioned SQLite-only hybrid retrieval with CJK/Latin terms, aliases, FTS5, literal LIKE fallback, weighted RRF, bilingual golden gates, and 100k reference benchmarks
- Official MCP Python SDK v2 stdio server with strict schemas, stable errors, modern/legacy compatibility, and v1 shims
- Atomic optional Obsidian projection, structured redacted logs, doctor/support bundle, and explicit signed updater
- Four-platform PyInstaller release workflow with native signing, notarization, Ed25519 manifest, SBOM, provenance, immutable evidence, and independent-review gates

This section is not a `1.0.0` release declaration. GA remains blocked until external signing, real-harness, clean-OS, conformance, and independent-review evidence passes on one frozen candidate.

## 0.1.0

- Public name: With. Command remains `eric-memory`
- Owned SQLite truth: `active` / `deprecated`, invalidate do not delete
- Official surface: CLI and stdio MCP share one set of verbs
- Entity-first search; CJK LIKE; Latin FTS5
- Read-only Holograph import; Obsidian is a projection
- Open harness catalog; harvest only approved roots
- Zero third-party runtime dependencies; Python 3.10+
- English-first public docs with a Chinese switch
