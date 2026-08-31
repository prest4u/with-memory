# Codex / OpenAI

ChatGPT cloud memory is not the local store. Do not resume a paused Codex holograph cron.

## MCP

Write this into `$HOME/.codex/config.toml`:

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
  --key codex --name "Codex / OpenAI" \
  --session-root "$HOME/.codex/sessions" \
  --harvest --mcp-mounted
```

Skills come from `$HOME/.agents/skills`.
