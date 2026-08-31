[English](daily-sync.md) · [中文](../每日同步任务.md)

# Quest: sync memory

You are updating the user’s local memory. Read `skills/严格技能.md` in this repository first, then act.

The product is With. The command is `eric-memory`.

## Hard limits

- Call only this repository’s CLI or MCP. Do not edit `memory.db` yourself.
- Search first. Expired rows in the default result are not current.
- Write one to three sentences: what / where / status / next door.
- On contradiction: `add` the new row, then `deprecate` the old one (or `add --supersedes`).
- Do not write credentials, secrets, a minor’s attendance or individual performance, or a whole transcript.
- Read only session directories the user already approved and registered. Do not scan the disk.
- Do not delete facts. `purge` is out of scope.

## 0. Locate

Find repository root `REPO` and data directory `DATA` (common: `$HOME/eric-memory-data` on macOS/Linux). If unsure:

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" status
```

If `status` fails, switch to the install quest. Do not invent a second store.

## 1. Search first

For today’s names, projects, and files:

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" search "$QUERY" --scope user
```

Add `--include-deprecated` only when you need history.

Scopes:

- Daily work: `--scope user`
- One project: `--scope project --project NAME`
- One workspace: `--scope workspace --workspace ABSOLUTE_PATH_OR_LABEL`

## 2. Read approved sources (optional)

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness list
```

Open only directories with `harvest_ok=true` and a non-empty `session_root`. Extract **pointers**. Do not paste chat logs into facts.

## 3. Write / invalidate

A current change:

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" add \
  --content "One-sentence fact. Paths are absolute. Status is current." \
  --entities "entity-a,entity-b" \
  --category project
```

Replace an old row:

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" add \
  --content "The new present." \
  --supersedes OLD_ID
```

Or:

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" deprecate OLD_ID \
  --superseded-by NEW_ID --reason "replaced by the new present"
```

## 4. Refresh the human window

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" sync
```

This rebuilds the Obsidian home / current / expired / folders / harness notes, and reindexes registered folders plus approved session roots. Files leave a path, not a body.

## 5. Tell the user

List:

- what you searched, which current rows hit
- which `#id` values you wrote
- which `#id` values you deprecated, and what replaced them
- whether the Obsidian home note was refreshed
- any contradiction that still needs a human decision

On Windows use `py -3` and quoted absolute paths.
