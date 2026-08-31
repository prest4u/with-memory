# Grok / xAI

Do not enable Grok's built-in `[memory]` section. That is a second store. Facts go through this repository.

## MCP

Write this into `$HOME/.grok/config.toml`:

```toml
[mcp_servers.eric-memory]
command = "python3"
args = [
  "/ABS/with-memory/mcp/server.py",
  "--data-dir",
  "/ABS/eric-memory-data",
]
startup_timeout_sec = 120
```

Session root after the user agrees:

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness add \
  --key grok --name "Grok / xAI" \
  --session-root "$HOME/.grok/sessions" \
  --harvest --mcp-mounted
```

`grok mcp list` should show `eric-memory`. Skills come from `$HOME/.agents/skills`.
