[English](release-process.md) · [中文](../发布流程.md)

# Release process and GA gates

The source version remains below 1.0 until every external gate passes. Local test success is necessary but cannot substitute for signing credentials, clean-OS installs, real harness environments, or independent review.

## CI

`.github/workflows/ci.yml` runs Python 3.10/3.12 on macOS arm64/x86_64, Windows x86_64, and Linux x86_64. It enforces Ruff, strict mypy, unit/integration tests, overall branch coverage, 95% core transaction/migration/permission branch coverage, bilingual retrieval quality, synthetic performance, dependency audit, secret scan, SBOM generation, and a weekly/manual 100k-fact/100k-file reference benchmark.

## Frozen release workflow

`.github/workflows/release.yml` is manual and bound to an existing `vVERSION` tag and full source commit. It checks source identity, benchmark results, and owner declarations for the following release requirements. Human reviewers must separately verify the declared work:

- `pyproject.toml`, `eric_memory.__version__`, tag, commit, and embedded Ed25519 public key agree;
- `release/evidence.json` records the owner's declaration of real end-to-end passes for Codex, Claude Code, Cursor, Hermes, Kimi, and Qwen;
- the owner declares that distinct security, migration, privacy, and release reviewers approved that same commit;
- the owner declares that a read-only real-database migration plan and verified backup were completed without modifying the real database;
- the owner declares that the official MCP conformance runner passed its active server suite against the shipping stdio server, with report hashes;
- the full 100k reference benchmark and all correctness/security gates pass.

The example contains invalid placeholders. Replacing them can satisfy schema checks; it cannot authenticate the declarations.

The `verify_release_evidence.py` script validates the shape of owner declarations and their version/commit references only. Its success does not establish that the reports exist, hashes match, reviewers are independent, or tests actually ran. Before approving either protected release job, the designated human reviewers must inspect the original reports, recompute their hashes, confirm reviewer identities and commit approvals, and check each real-harness, migration and conformance result. Keep private reports outside the public repository. The current automation alone cannot certify GA readiness.

## Native candidates

PyInstaller builds separately on each target OS; cross-compilation is rejected by `package_release.py`. Each candidate runs the frozen `init → candidate → review → search → backup → restore → doctor → support-bundle → MCP` workflow with only synthetic temporary data. First and warm CLI startup, plus MCP initialization, are release gates.

macOS binaries require Developer ID signing and notarization. Windows requires Authenticode plus timestamp verification. All four archives are covered by one Ed25519-signed manifest, SHA-256 checksums, CycloneDX SBOM, and GitHub build provenance. Both native signing/notarization and final manifest signing/publication use the `production-release` environment. Configure required reviewers and permitted release refs on that environment, and keep all signing and notarization credentials as environment secrets. Each job must receive approval before it can access those credentials. Publication uses an existing tag and never silently creates one.

## MCP conformance

With. pins the official MCP Python SDK v2 and tests the real stdio server with the official SDK client, strict tool JSON Schemas, malformed JSON-RPC, legacy negotiation, least-privilege discovery, and frozen-binary smoke. The upstream official conformance runner currently accepts only an HTTP URL for server testing; stdio server support remains an [open upstream request](https://github.com/modelcontextprotocol/conformance/issues/258). Because v1 deliberately ships no HTTP listener, official server-suite execution remains an explicit GA evidence gap until upstream adds stdio support. `verify_release_evidence.py` requires a real `stdio`/`shipping-server` pass and the checked-in example declares that result blocked; HTTP or SDK-fixture results cannot be relabeled as product conformance.

## Release order

1. Tag and build `1.0.0-rc.1`; migrate only synthetic databases and copies.
2. Run a read-only real-library migration plan and create/verify a full backup with owner approval.
3. Freeze the commit and evidence; obtain all independent reviews.
4. Build, sign, notarize, attest, and inspect all four immutable candidates.
5. Publish the RC and complete clean-user installs and six real harness smokes.
6. Repeat the frozen process for `1.0.0`. Any corruption, permission bypass, migration mismatch, unsigned artifact, missing evidence, or core harness failure blocks GA.

No workflow migrates an owner's real database or publishes a release merely because this repository was edited.
