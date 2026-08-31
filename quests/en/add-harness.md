[English](add-harness.md) · [中文](../添加AI工具.md)

# Quest: add an AI tool

The kernel is already installed. Register one new harness. Do not reinstall, do not change the database, do not bend to a vendor plugin.

The product is With. The command is `eric-memory`.

## Hard limits

- Do not create a second data directory.
- Do not search the disk for session files. The user gives the path, or confirms that a catalog “typical path” exists after expansion.
- No session directory means CLI / MCP only.
- An unexpanded `~` must not be written.
- If GLM is only a model inside another tool, do not create a stand-alone “GLM harness” unless the user actually uses ZCode or another independent client.

## 1. Ask

1. Tool name (check `eric-memory harness catalog` first).
2. Can this tool already run a terminal command?
3. Should MCP be mounted (stdio: `mcp/server.py` in this repository)?
4. Is there a session directory that may be indexed on sync (absolute path)? If not, harvest stays off.
5. Does a mainland-network limit affect it (Kimi purchases, Cursor/OpenAI/Grok)? Record it. Do not bypass it.

## 2. Register

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness catalog
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness add \
  --key "$KEY" \
  --name "$DISPLAY_NAME"
```

After the user approves a session directory:

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness add \
  --key "$KEY" \
  --name "$DISPLAY_NAME" \
  --session-root "$ABS_SESSION" \
  --harvest
```

Add `--mcp-mounted` when MCP is actually written into the host config. See `adapters/` for a matching note; otherwise `adapters/generic-mcp.md`.

## 3. Give this tool the skill

Put `skills/严格技能.md` where the tool can read it (project skill, user skill, or pinned on the next turn). The points do not move:

- search first
- write only through CLI/MCP
- one to three sentence pointers
- deprecate the old row on contradiction
- harvest only registered sources

## 4. Project

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" sync
```

Ask the user to open the Obsidian harness note and confirm the new name.

## 5. Small acceptance

In the new tool, search a current fact you just wrote; write a test pointer and deprecate it. Only then tell the user the tool is connected.
