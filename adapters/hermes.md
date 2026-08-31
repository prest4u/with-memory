# Hermes Agent

## Do not

- Do not install this system into the old Holograph plugin tree
- Do not edit `$HOME/.hermes/profiles/*/memory_store.db`
- After import and a live verify, clear `memory.provider`. The old store is archive-only.

## CLI

Replace the placeholders with absolute paths on this machine:

```bash
python3 /ABS/with-memory/bin/eric-memory --data-dir /ABS/eric-memory-data search "…"
```

## Session directory

Register only the sessions folder, not the whole Hermes tree:

```bash
python3 /ABS/with-memory/bin/eric-memory --data-dir /ABS/eric-memory-data \
  harness add --key hermes --name "Hermes Agent" \
  --session-root /ABS/hermes-profile/sessions \
  --harvest --mcp-mounted
```

## MCP

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

After import + verify: remove `memory.provider: holographic` (empty = builtin MEMORY.md / USER.md only). Keep `memory_enabled` and `user_profile_enabled`. New fact writes go through eric-memory MCP or CLI.

After a Hermes restart, tool names are usually prefixed like `mcp_eric-memory_memory_search`. Do not `INSERT` into `memory_store.db`.
