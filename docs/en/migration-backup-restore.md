[English](migration-backup-restore.md) · [中文](../迁移备份与恢复.md)

# Migration, backup, and restore

## Connection safety

Database opening has three explicit modes: read-only, read/write-existing, and create. `search`, `status`, `doctor`, `history`, `export`, and `verify` use a read-only SQLite URI. Only `init` may create a missing database.

## v1 to schema v2

Stop all older v1 CLI and MCP processes that access this library before migration. They do not use the v2 writer lock and can write to a replaced database or prevent replacement on Windows. After migration, reconnect clients using the upgraded CLI and MCP server.

`migrate --apply` obtains an exclusive cross-platform lock, makes a verified backup, converts into a separate v2 temporary database, validates integrity, foreign keys, row counts, IDs, entity links, FTS rows, statuses, and supersession chains, then atomically replaces the original path. A conversion failure leaves the original database in place.

Legacy integer fact IDs, timestamps, states, entities, harnesses, file records, audit metadata, and configured Vault path are retained. Legacy facts receive deterministic UUIDv5 identities derived from the library UID and integer ID. New objects use UUIDv4.

Well-formed `project:` and `workspace:` tags become structured scopes. Ambiguous tags remain unchanged in user scope and are reported by `doctor`.

## Backups

```bash
eric-memory --data-dir /ABS/data backup create
eric-memory --data-dir /ABS/data backup list
eric-memory --data-dir /ABS/data backup verify /ABS/data/backups/FILE.sqlite3
eric-memory --data-dir /ABS/data backup prune
```

Every backup has a metadata record, SHA-256, SQLite integrity result, schema version, fact count, and verified status. The first write opportunity each day creates a daily backup. Rotation retains seven daily, four weekly, and twelve monthly generations. Migration, restore, purge, and update create purpose-specific safety snapshots first and stop if that snapshot fails.

Copying only `memory.db` while WAL activity is possible is not a supported backup. Use the command.

## Restore

```bash
eric-memory --data-dir /ABS/data restore --from /ABS/backup.sqlite3
```

Restore verifies the input, locks the database, snapshots the current state, restores through a temporary path, atomically activates it, hardens permissions, and runs integrity checks. The local terminal requires typing `RESTORE`. MCP does not expose restore.

An optional working-context cache is excluded from backups. Restore removes that
whole cache and its source-enable settings before changing the main database, so
older consent records cannot reactivate invalidated documents. If cache cleanup
fails, restore stops. Enable selected temporary sources again after recovery.

## Exact rollback boundary

Rollback is lossless only before the first v2 write. After a v2 write, With. first exports the v2-only increment, displays potential loss, and requires typing `ROLLBACK`:

```bash
eric-memory --data-dir /ABS/data migrate --rollback \
  --incremental-export /ABS/data/exports/pre-v1-rollback-increment.jsonl
```

Keep the export and restored backup together. The v1 database cannot represent v2 candidates, principals, structured scopes, or source cursors.

## Recovery check

After migration or restore, run:

```bash
eric-memory --data-dir /ABS/data doctor
eric-memory --data-dir /ABS/data verify --expect-query "known entity"
eric-memory --data-dir /ABS/data sync
```

A projection failure does not roll back a committed database write. The result reports `committed=true` and `projection=dirty`; `sync` repairs the optional projection.

A successful database change returns `committed: true` even if subsequent configuration or operation-log writes fail. `configuration: pending` and `operation_log.status: failed` identify those follow-up failures. Inspect the current schema with `doctor`, fix write permissions, and repair the configuration; do not rerun a committed migration or restore merely to retry a log write. Restore validates full-text postings and structured search terms before replacing the current database.

Restore accepts closed standalone backup files. A source with pending WAL writes is rejected; create a fresh backup through `backup create` before retrying.

Before restore, stop or disconnect every CLI/MCP process using the target database. Windows cannot replace a database file held open by another SQLite connection.
