# Kimi Code / Kimi Desktop

## Kimi Code

Typical session root after expanding `$HOME` and confirming it exists:

```text
$HOME/.kimi-code/sessions
```

MCP goes in `$HOME/.kimi-code/mcp.json`:

Install the repository dependencies into a dedicated runtime first, then use its absolute Python path. A system `python3` without the official MCP SDK exits before connecting:

```bash
python3 -m venv "$REPO/.venv"
"$REPO/.venv/bin/python" -m pip install -e "$REPO"
```

```json
{
  "mcpServers": {
    "eric-memory": {
      "command": "/ABS/with-memory/.venv/bin/python",
      "args": [
        "/ABS/with-memory/mcp/server.py",
        "--data-dir",
        "/ABS/eric-memory-data",
        "--principal",
        "kimi"
      ]
    }
  }
}
```

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness add \
  --key kimi --name "Kimi Code" --mcp-mounted
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" source approve \
  "$HOME/.kimi-code/sessions" --harness kimi
```

If this machine still has leftover `$HOME/.kimi/sessions` from a migration, register that separately. Do not write the KimiCU binary.

Kimi Code project `.kimi-code/mcp.json` entries override the user entry with the same name. Keep `--principal kimi` in both when present. After changing a server, start a new Kimi session and inspect `/mcp`. See the [official Kimi Code MCP documentation](https://moonshotai.github.io/kimi-code/en/customization/mcp).

Mainland note: consumer new purchases were paused. Use whatever is already installed; do not steer people to unofficial channels.
