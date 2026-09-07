[English](privacy-threat-model.md) · [中文](../隐私与威胁模型.md)

# Privacy and threat model

## Assets

With. protects fact text, candidate text, entities, source locators, file paths and hashes, scope membership, audit metadata, and backups. SQLite is the truth; an enabled Obsidian Vault is a separate plaintext projection.

## Boundary

The final security boundary is the operating-system account and its disk encryption. With. applies least privilege inside MCP, filesystem permissions, source consent, redacted logs, signed updates, and content controls. It does not claim to resist an arbitrary process with shell access as the same user, a privileged administrator, offline disk forensics, or compromised OS libraries.

Use FileVault, BitLocker, or LUKS. With. does not implement application-level database encryption. `doctor` reports when disk encryption cannot be verified; that warning is not proof that encryption is absent on every platform.

## MCP principals and scopes

Every non-legacy MCP configuration must pass `--principal KEY`. `tools/list` is capability-filtered. Default harness capabilities are:

- `status:read`
- `fact:search` for authorized active scopes
- `candidate:submit`
- `candidate:read-own`
- `source:scan` for assigned sources

History, direct fact writes, sync, and file search require a local CLI grant. The compatibility `manage` grant allows a harness to list harness identities. Import, file-index administration, and harness registration remain local-administrator operations and are hidden from other principals even with that grant. Project and workspace queries require an exact key. `scope=user` means every scope granted to that principal, not a way to bypass ACLs. Status, scope, and ACL filters run before ranking.

History by fact ID requires both `fact:search` and `history:read` for every fact in the returned chain. Source revocation rejects pending candidates from that source and clears their bodies. Expired candidates are cleared before viewing or review, and acceptance rechecks expiry and current source consent inside the write transaction.

The `legacy` principal supports status and active search only. Legacy `memory_add` and `memory_deprecate` calls return stable `PERMISSION_REQUIRED` errors.

## Content controls

Before database insertion, With. checks candidate and import text for credentials, private keys, high-entropy tokens, cookies, long source text, and suspected minor attendance or individual performance.

- A definite prohibited match is rejected with zero original-text persistence in facts, candidates, logs, or audit records.
- Suspicious text produces only a redacted quarantine record and must be resubmitted after redaction.
- Accepted candidates remove their candidate body; the body lives only on the immutable fact.
- Rejected and expired candidates immediately remove their body and retain only a non-reconstructive summary, reason, and audit metadata.

No MCP parameter bypasses prohibited categories.

## Sources

Only local interactive `source approve` grants access. It stores a canonical root, include/exclude rules, allowed file types, file-size limit, owning harness, and consent event. OS roots are forbidden; approving the complete home directory requires typing its canonical path. Scans use `lstat`, skip every symlink, and re-check containment.

The database stores paths, hashes, file state, cursors, and candidate references—not file or session bodies. Revocation invalidates open runs, clears file indexes and cursors, and marks derived facts as source-revoked without silently deleting them.

## Logs and support

Structured logs carry an operation UID but omit fact bodies and full paths by default. `support-bundle` exports versions, schema, redacted configuration, and logs; facts and paths are excluded unless a future explicit surface separately requests them. Never attach a live database or raw session to a public issue.

## Network and supply chain

There is no telemetry. Only explicit update commands use the network. Update URLs are restricted to approved GitHub HTTPS hosts, every redirect is checked before use, archives are bounded and traversal-safe, and the final asset must match an Ed25519-signed manifest. Missing trust roots or signatures stop the update.
