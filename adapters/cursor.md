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
      "type": "stdio",
      "command": "/ABS/with-memory/.venv/bin/python",
      "args": [
        "/ABS/with-memory/mcp/server.py",
        "--data-dir",
        "/ABS/eric-memory-data",
        "--principal",
        "cursor"
      ]
    }
  }
}
```

Register it first with local `harness add --key cursor --name Cursor --mcp-mounted`. Write the block into project `.cursor/mcp.json` or the user-level file. A new chat should see search and candidate tools, while direct fact writes remain hidden.

Put `skills/严格技能.md` in project or user rules. Do not approve the whole workspace as a source unless the user clearly agrees; consent must use local `source approve ... --harness cursor`.

Use a dedicated runtime with the repository dependencies installed. Check both user and project MCP entries for `--principal cursor`.

When the user authorizes session ingestion and the directory exists, current local Agent transcripts can be registered separately from source code and IDE caches:

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" source approve \
  "$HOME/.cursor/projects" --harness cursor \
  --include '*/agent-transcripts/*.jsonl' --include '*/agent-transcripts/*.txt' \
  --include '*/agent-transcripts/*.md' --type jsonl --type txt --type md
```

Only use returned changed paths from `harvest begin`. Cloud chats or sessions without a local transcript need an explicit export into an approved source; do not treat the entire Cursor workspace database as a transcript.

Optional [working context](../docs/en/working-context.md) uses this same server. After explicit project grants, restart the client to discover `memory_context_index`, `memory_recall`, and `memory_context_read`. Capture verbose command output in an approved file before indexing; no Cursor hook installation is required for this explicit workflow.
