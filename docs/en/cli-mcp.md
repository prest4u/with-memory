[English](cli-mcp.md) · [中文](../../README.zh-CN.md)

# CLI and MCP

The product is With. The command is `eric-memory`. Both surfaces call the same service.

Global flags can sit anywhere on the line: `--data-dir`, `--json`, `--version` / `-V`.

## CLI

```bash
python3 /ABS/with-memory/bin/eric-memory --data-dir "$HOME/eric-memory-data" status
python3 /ABS/with-memory/bin/eric-memory --data-dir "$HOME/eric-memory-data" search "query" --scope user
python3 /ABS/with-memory/bin/eric-memory --data-dir "$HOME/eric-memory-data" add \
  --content "One to three sentences. Paths are absolute. Status is current." \
  --entities "entity-a,entity-b"
python3 /ABS/with-memory/bin/eric-memory --data-dir "$HOME/eric-memory-data" deprecate 12 \
  --superseded-by 13 --reason "replaced by the new present"
python3 /ABS/with-memory/bin/eric-memory --data-dir "$HOME/eric-memory-data" sync
```

Useful extras:

| Command | Role |
| --- | --- |
| `init --tier simple\|full --vault …` | Create the data directory and vault templates |
| `import holograph --source …` | Read-only import |
| `index-files --folder …` | Register a folder of documents |
| `harness add --key … --name …` | Register a tool |
| `harness list` / `harness catalog` | See what is registered / known |
| `verify --expect-query …` | Maintainer check after import |

Windows: `py -3` and `%USERPROFILE%\eric-memory-data`.

## MCP

stdio server:

```text
python3 /ABS/with-memory/mcp/server.py --data-dir /ABS/eric-memory-data
```

| CLI | MCP |
| --- | --- |
| `status` | `memory_status` |
| `add` | `memory_add` |
| `search` | `memory_search` |
| `deprecate` | `memory_deprecate` |
| `sync` | `memory_sync` |
| `import holograph` | `memory_import` |
| `index-files` | `memory_index_files` |
| `harness add` | `memory_harness_add` |
| `harness list` | `memory_harness_list` |

Parameters match the CLI. Do not invent a second set of names.

A Cursor template is at [`.cursor/mcp.json.example`](../../.cursor/mcp.json.example). Host notes live under [`adapters/`](../../adapters/). Writing a flag that says MCP is mounted, without writing the host config, does not count as mounted.

## Contract

Read [skills/严格技能.md](../../skills/严格技能.md) before a write. Search first. One to three sentences. Deprecate on contradiction. Harvest only approved roots. Never put secrets or a minor’s attendance into a fact.
