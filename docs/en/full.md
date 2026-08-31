[English](full.md) · [中文](../完整.md)

# Full tier

Everything in the simple tier still holds. This page is only what a maintainer needs.

## Layout

Use absolute paths after init. Expand `~` or `%USERPROFILE%` once.

- Repository: the clone of this project
- Default data directory: `$HOME/eric-memory-data` on macOS/Linux, `%USERPROFILE%\eric-memory-data` on Windows
- Truth: `memory.db` inside that directory
- Human vault: `vault` under the data directory, unless you passed `--vault`
- Old Holograph: a path the user gives, often `$HOME/.hermes/profiles/<name>/memory_store.db`. Read-only. Do not edit or delete it.

Do not commit the data directory.

## Develop

```bash
cd /ABS/with-memory
python3 -m unittest discover -s tests -v
python3 scripts/repo_check.py
python3 bin/eric-memory --data-dir "$HOME/eric-memory-data" init --tier full
python3 bin/eric-memory --data-dir "$HOME/eric-memory-data" import holograph \
  --source "$HOME/.hermes/profiles/NAME/memory_store.db"
python3 bin/eric-memory --data-dir "$HOME/eric-memory-data" verify \
  --expect-query "example-name"
python3 bin/eric-memory --data-dir "$HOME/eric-memory-data" sync
```

MCP entry: `mcp/server.py` (stdio, `Content-Length` frames). Tool names match CLI verbs.

On a machine that already has a live data directory, `python3 scripts/acceptance_check.py` is extra. CI only runs unit tests and `repo_check.py`.

## Import rules

- Move content, category, tags, entities, trust
- Keep rows that already carry `status:deprecated`
- Mark untagged rows `active`, with `as_of` set to the import day
- Do not guess deprecations while importing
- Dedupe on `source_kind=import` plus `source_ref=holograph:<id>` so a rerun is safe

Until import succeeds, the old Holograph store can stay in use. After it succeeds, new writes go through this repository. Keep the old file as an archive.

## Search

1. Entity names
2. CJK: LIKE (AND across tokens; mixed queries are split)
3. Latin: FTS5, then LIKE on a miss

Default `status = active`. History needs `--include-deprecated`.

## Catalog

`eric-memory harness catalog` prints known keys. It is not a closed world. A new tool uses the add-harness quest. The kernel is not rewritten.

Daily sync harvests only rows with `harvest_ok=1` and an absolute `session_root`. Unregistered directories are not walked.

## Windows / Linux

Same contract. See [windows-linux.md](windows-linux.md). You do not have to prove those hosts yourself.

- Windows: `py -3`, data directory `%USERPROFILE%\eric-memory-data`
- Linux: `python3`, data directory `$HOME/eric-memory-data`
- Never install as Administrator or root

## Not in v1

Client zip, cloud sync, purge on the simple tier, restoring a large paused Codex cron.
