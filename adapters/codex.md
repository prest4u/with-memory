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
  "--principal",
  "codex",
]
startup_timeout_sec = 120
```

Session root after the user agrees:

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness add \
  --key codex --name "Codex / OpenAI" --mcp-mounted
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" source approve \
  "$HOME/.codex/sessions" --harness codex
```

Skills come from `$HOME/.agents/skills`. The harness submits candidates; local interactive review is the default activation path.

For optional [working context](../docs/en/working-context.md), keep this server entry and grant the project-specific context capabilities. Use an absolute executable from the installed package or its dedicated virtual environment. Save large tool output to an approved file through Codex's existing command runner, then index it. In code mode, avoid printing raw results that will be captured and searched. This release does not install interception hooks or change your `AGENTS.md` automatically.
