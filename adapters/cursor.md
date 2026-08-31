# Cursor

## CLI

Cursor Agent calls the same command. Replace the placeholders with absolute paths on this machine:

```bash
python3 /ABS/with-memory/bin/eric-memory --data-dir /ABS/eric-memory-data search "…"
```

## MCP

Add a stdio server in Cursor MCP settings:

```json
{
  "mcpServers": {
    "eric-memory": {
      "command": "python3",
      "args": [
        "/ABS/with-memory/mcp/server.py",
        "--data-dir",
        "/ABS/eric-memory-data"
      ]
    }
  }
}
```

Write that into the project `.cursor/mcp.json` or the user-level `~/.cursor/mcp.json`. An empty file can be replaced with the block above. A new Cursor chat should then see `memory_search`.

Put `skills/严格技能.md` in the project or user rules. Do not register the whole Cursor workspace as a harvestable session root unless the user clearly agrees.
