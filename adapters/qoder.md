# Qoder

If the session path is unknown, mount CLI / MCP only. Do not harvest the whole Qoder home (plugins and cache).

## MCP

Write this into the Qoder user `mcp.json`:

```json
{
  "mcpServers": {
    "eric-memory": {
      "command": "python3",
      "args": [
        "/ABS/with-memory/mcp/server.py",
        "--data-dir",
        "/ABS/eric-memory-data",
        "--principal",
        "lingma"
      ]
    }
  }
}
```

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness add \
  --key lingma --name "Qoder" --mcp-mounted
```

The catalog key is `lingma`.

If the user later supplies a source path, approve it separately with local `source approve PATH --harness lingma`. Qoder submits candidates by default.
