[English](SECURITY.md) · [中文](SECURITY.zh-CN.md)

# Security policy

## Supported versions

Before GA, only the latest frozen release candidate is eligible for security fixes. After `1.0.0`, the latest v1 minor/patch line receives security fixes; older development snapshots are unsupported. A release is authentic only when its native archive verifies against the attached Ed25519-signed manifest and GitHub provenance.

## Report privately

Use GitHub's private vulnerability reporting / Security Advisory for this repository. If that channel is unavailable, contact the maintainer through a private channel already listed on the GitHub profile and ask for a security-report route without including the vulnerability details in the first message.

Include the affected version/commit, OS and architecture, impact, minimal synthetic-data reproduction, and any redacted operation UID. Do not send a live `memory.db`, WAL, raw session/file body, full path inventory, credential, Vault, release private key, or unredacted support bundle.

Do not open a public issue until a coordinated disclosure date is agreed. The maintainer should acknowledge a complete report within seven days, triage severity and affected versions, prepare a private fix and regression test, rotate compromised signing material when relevant, and publish an advisory with the fixed release.

## Security invariants

- SQLite is the only truth; no command edits it outside the transaction/service boundary.
- Ordinary MCP principals cannot approve candidates, purge, restore, migrate, approve sources, or manage grants.
- ACL, scope, and status filtering happens before ranking. Deprecated and cross-scope leakage are release blockers.
- Prohibited content must have zero original-text persistence. Logs and audit events do not duplicate fact text.
- Non-`init` commands cannot create a missing database. Read commands use a read-only URI.
- Updates occur only when explicitly invoked and fail closed on a missing/invalid trust root, signature, size, hash, redirect, or archive path.
- Source scans never follow symlinks and never walk an unapproved root.

The final boundary is the OS account. Arbitrary shell execution as the same user is local-admin authority. Enable FileVault, BitLocker, or LUKS. See the full [privacy and threat model](docs/en/privacy-threat-model.md) and [emergency purge limits](docs/en/purge.md).
