[English](troubleshooting-support.md) · [中文](../故障排查与支持.md)

# Troubleshooting and support

Start with read-only diagnostics:

```bash
eric-memory --data-dir /ABS/data status
eric-memory --data-dir /ABS/data doctor
eric-memory --data-dir /ABS/data doctor --json
```

## Database missing

Read commands intentionally do not create a database. Confirm the absolute data directory. Run `init` only when you intend to create a new library; do not initialize over a guessed path.

## `PERMISSION_REQUIRED`

Check that MCP starts with `--principal KEY`, that the principal exists in `harness list`, and that it has the required capability/scope. Omitting the principal creates a read-only legacy identity. Grant only the missing capability from the local CLI.

## Search returns nothing

Confirm the exact project/workspace key, source status, and fact status. Default search excludes deprecated facts and stale files. `%` and `_` are literal query characters, not wildcards. Only an identity with `history:read` can request deprecated history.

Run `verify --expect-query "known entity"` after migration. A zero result is not permission to bypass ACLs or scan the disk.

## Source scan is truncated or stale

`source scan` reports `truncated` or an error instead of silently dropping records. Narrow include rules, exclude generated directories, or raise the explicit limit within the supported 100,000-file target. Missing roots and files become stale immediately and disappear from search. Symlinks are always skipped.

Revoking and re-approving a source is a new consent event and resets its cursor.

## Projection is dirty

The SQLite write may already be committed. Inspect `committed` and `projection` in the result, fix Vault permissions or availability, then run `sync`. Do not repeat the database write merely because projection failed.

## Backup or restore fails

Use `backup verify` on the exact absolute file. Ensure enough free disk space for the current database, a safety snapshot, and a temporary restored copy. Do not copy a live WAL database manually.

## Migration fails

The original must remain at its path. Preserve the error and pre-migration backup, run `doctor` against a copy, and do not repeatedly apply migration to the real database. Scope-tag warnings require human classification but do not silently discard the original tag.

## MCP host fails to start

Run the exact command in a terminal with `--version`, then `status`. stdio stdout is protocol-only; diagnostics belong on stderr. The official server is newline-delimited JSON-RPC and supports modern and legacy protocol clients through the pinned MCP Python SDK. Do not configure an HTTP URL.

## Redacted support bundle

```bash
eric-memory --data-dir /ABS/data support-bundle --output /ABS/with-support.json
```

Open the JSON before sharing it. By default it contains no facts or full paths. Never upload `memory.db`, WAL files, raw source/session text, release private keys, or a complete Vault to a public issue.

Security reports should follow [`SECURITY.md`](../../SECURITY.md). Include With. version, OS/architecture, schema version, operation UID, redacted error code, and reproducible steps using synthetic data.
