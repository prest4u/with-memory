# Hermes Agent

## Do not

- Do not install this system into the old Holograph plugin tree
- Do not edit `~/.hermes/profiles/*/memory_store.db`
- Until import succeeds, the old Holograph store can stay in use; after it succeeds, new writes go through this repository

## CLI

Replace the placeholders with absolute paths on this machine:

```bash
python3 /ABS/with-memory/bin/eric-memory --data-dir /ABS/eric-memory-data search "…"
```

## Session directory

Register only when the user agrees, for example:

```bash
python3 /ABS/with-memory/bin/eric-memory --data-dir /ABS/eric-memory-data \
  harness add --key hermes --name "Hermes Agent" \
  --session-root /ABS/hermes-profile \
  --harvest
```

Harvest indexes file paths. It does not write `memory_store.db` back into the old format.

## MCP

Add this to the profile `config.yaml`. Do not change `memory.provider: holographic`; the old store stays an archive:

```yaml
mcp_servers:
  eric-memory:
    command: python3
    args:
      - /ABS/with-memory/mcp/server.py
      - --data-dir
      - /ABS/eric-memory-data
    timeout: 120
```

After a Hermes restart, tool names are usually prefixed like `mcp_eric-memory_memory_search`. New writes go through those tools or the CLI. Do not `INSERT` into `memory_store.db`.
