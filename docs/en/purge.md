[English](purge.md) · [中文](../紧急清除.md)

# Emergency purge

`purge` is a destructive incident-response operation, not normal memory evolution. Ordinary changes create a replacement fact and deprecate the old one.

## Before running

1. Stop every configured MCP host and any process using the Vault.
2. Run `doctor` and inspect the exact fact with `history FACT_ID`.
3. Check whether the text exists in Time Machine, OS snapshots, cloud drives, Obsidian Sync, or other third-party copies. With. cannot erase those copies.
4. If a recoverable correction is sufficient, use supersession instead.

## Command

```bash
eric-memory --data-dir /ABS/data purge FACT_ID
```

The command displays the fact UID, sources, projection impact, and managed backups. It proceeds only after the local user types the exact fact UID. MCP never exposes purge.

## What With. removes

- the fact body, entity links, scope links, search terms, FTS row, source links and supersession edges;
- linked source locators and candidate references that could preserve the target text;
- With.-owned Obsidian projection pages, followed by a clean regeneration;
- every With.-managed automatic backup containing the target;
- the entire optional working-context cache and its source-enable settings;
- SQLite WAL remnants through secure delete, checkpoint and VACUUM.

Afterward it runs integrity checks and creates a new clean backup. The only retained tombstone identifies that a purge occurred; it contains no body or content hash.

If projection preflight fails, database deletion does not begin. If regeneration fails after database purge, With. removes all With.-owned projection pages rather than leaving stale content.

The preview reports whether a working-context cache exists. Its removal happens
before the fact is purged; failure prevents the main-database deletion. Re-enable
selected temporary sources afterward. Original source files remain outside this
cleanup and can still contain the target text.

## What With. cannot remove

Time Machine, filesystem snapshots, forensic remnants outside SQLite's guarantees, copied exports, screenshots, shell history, third-party sync, and manually copied Vault pages are outside With.'s control. Follow the relevant provider's deletion and retention procedure separately.

If `committed` is true and `purge_cleanup` is `pending`, the fact has already been removed. Check `remaining_managed_backups`; run `backup remove PATH...` with those exact paths and confirm `REMOVE BACKUPS`, then run `backup create`. Fix filesystem permissions before retrying. Do not repeat purge using the deleted fact ID.
