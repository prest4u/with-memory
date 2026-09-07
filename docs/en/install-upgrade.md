[English](install-upgrade.md) · [中文](../安装与升级.md)

# Install and upgrade

## Supported shape

With. is installed per user. It does not require administrator rights, add itself to `PATH`, start a daemon, or contact a network during installation. GA targets are macOS arm64/x86_64, Windows x86_64, and Linux x86_64. A build is an official release only when its archive, signed manifest, SBOM, provenance, and native platform signature are attached to the same GitHub Release.

This repository is currently a development checkout, not a GA binary distribution.

## Install from source

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade "pip==26.2.1"
.venv/bin/python -m pip install .
.venv/bin/eric-memory --data-dir "$HOME/eric-memory-data" init --no-obsidian
```

To enable an Obsidian projection, omit `--no-obsidian` or provide an absolute `--vault` path. A pre-v1 `--tier simple|full` argument remains accepted but is ignored with a deprecation warning.

On Windows, use `.venv\Scripts\python.exe` and `.venv\Scripts\eric-memory.exe`. Use a data directory owned only by the current user. `doctor` reports permissions and whether OS disk encryption can be verified.

## Initialize principals

Create each harness locally:

```bash
eric-memory --data-dir /ABS/data harness add --key cursor --name Cursor --mcp-mounted
```

It receives status, authorized active-fact search, candidate submission/own-candidate read, and assigned-source scan capabilities. Add exceptional capabilities only from the local CLI:

```bash
eric-memory --data-dir /ABS/data harness grant --key cursor --capability history:read
```

`fact:write`, `sync:run`, `file:search`, and `manage` are not default grants.

## Explicit updater

With. never checks for updates in the background:

```bash
eric-memory update check
eric-memory --data-dir /ABS/data update apply
```

`update apply` downloads only approved GitHub HTTPS assets, verifies the embedded Ed25519 trust root, signed manifest, asset size and SHA-256, rejects unsafe archives, displays the database migration plan and backup location, and requires the exact version confirmation. It installs into a user directory and does not modify `PATH`. A schema migration remains a separate explicit command.

If the embedded public key is absent, update application fails closed. Do not work around that check.

## Existing database upgrade

Run read-only planning first:

```bash
eric-memory --data-dir /ABS/data migrate --plan
eric-memory --data-dir /ABS/data backup create
```

Only after inspecting the plan and verifying a backup:

```bash
eric-memory --data-dir /ABS/data migrate --apply
eric-memory --data-dir /ABS/data doctor
```

Never test migration against the only copy of a real database. RC validation uses a copied database; migrating the owner's real database requires separate explicit approval.

See [Migration, backup, and restore](migration-backup-restore.md) before upgrading a pre-v2 database.
