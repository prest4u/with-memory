# Kimi Code / Kimi Desktop

## Kimi Code

Typical session root after expanding `$HOME` and confirming it exists:

```text
$HOME/.kimi-code/sessions
```

MCP goes in `$HOME/.kimi-code/mcp.json`:

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

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness add \
  --key kimi --name "Kimi Code" \
  --session-root "$HOME/.kimi-code/sessions" \
  --harvest --mcp-mounted
```

If this machine still has leftover `$HOME/.kimi/sessions` from a migration, register that separately. Do not write the KimiCU binary.

Mainland note: consumer new purchases were paused. Use whatever is already installed; do not steer people to unofficial channels.
