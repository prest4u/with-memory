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
  harness add --key hermes --name "Hermes Agent" --mcp-mounted
python3 /ABS/with-memory/bin/eric-memory --data-dir /ABS/eric-memory-data \
  source approve /ABS/hermes-profile/sessions --harness hermes
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
      - --principal
      - hermes
    timeout: 120
```

After import + verify: remove `memory.provider: holographic` (empty = builtin MEMORY.md / USER.md only). Keep `memory_enabled` and `user_profile_enabled`. New memories enter as candidates and require local review by default.

After a Hermes restart, tool names are usually prefixed like `mcp_eric-memory_memory_search`. Do not `INSERT` into `memory_store.db`.
