[English](install.md) · [中文](../安装任务.md)

# Quest: install local memory

You are the install agent. The user has this repository on their machine. Finish the steps below. Do not turn this into “install an app” or “install a login daemon.”

The product is With. The command is `eric-memory`.

## Hard limits

- No Administrator / sudo / elevated terminal.
- Do not change the system PATH unless the user says you may.
- Do not install a daemon, login item, or scheduled task (later automation is a different quest).
- Do not connect to a cloud. Do not create a GitHub / Gitee repository for the user.
- Do not modify an existing Holograph / Hermes `memory_store.db`.
- Once a path is written to config, it must be absolute. Never store an unexpanded `~`.
- Call only this repository’s `bin/eric-memory`. No private SQL `INSERT`. From a raw clone use `python3 bin/eric-memory` (that entry puts `src` on the path). `python3 -m eric_memory` is not official; if you want it, set `PYTHONPATH=src` or `pip install -e .` first.

## 0. Find the repository

The root should contain `bin/eric-memory`, `src/eric_memory/`, and `quests/`.

If the current workspace is not that root, ask where the clone lives, then `cd` there. Call that absolute path `REPO`.

## 1. Check the runtime

macOS / Linux:

```bash
python3 --version
```

Windows:

```bat
py -3 --version
```

Need Python 3.10+. If it is missing, stop. Tell the user to install official Python. Do not install a pile of dependencies as Administrator. This system has zero third-party packages.

## 2. Ask (must ask, do not guess)

Ask in the user’s language and wait:

1. **Tier**: simple (default) or full (maintainer)?
2. **Data directory**: default `$HOME/eric-memory-data` on macOS/Linux, `%USERPROFILE%\eric-memory-data` on Windows. If they want another place, they give an absolute path.
3. **Obsidian vault**: default `vault` under the data directory. An existing vault must be an absolute path.
4. **AI tools in use** (multi-select; not a closed list):
   - Kimi Code
   - Qwen Code
   - Lingma / Qoder
   - WorkBuddy
   - CodeBuddy
   - ZCode (GLM is often only a model; tick ZCode only if they really use it)
   - MiniMax Code
   - Trae / TraeCode
   - Comate
   - OpenClaw
   - Cursor
   - Claude Code / Codex / OpenAI
   - Hermes
   - Grok / xAI
   - Other (ask them to name it)
5. For each ticked tool: is there a **user-approved** session directory? If yes, take the absolute path. If no, mount CLI/MCP only. **Do not search the disk.**
6. **Document folders** that may be indexed later. Skip if none. Absolute paths if any.
7. Mainland-network note (inform, do not bypass):
   - New Kimi consumer purchases have been paused; go by what is already installed
   - Cursor / OpenAI / Grok may be limited in mainland China
   - Tools can be added later with the add-harness quest

## 3. Initialize

Expand `~` or `%USERPROFILE%` to an absolute path here, then run.

macOS / Linux:

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" init --tier simple --vault "$VAULT"
```

Full tier: `--tier full`.

For each document folder:

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" index-files --folder "$FOLDER"
```

Windows (PowerShell):

```powershell
py -3 "$REPO\bin\eric-memory" --data-dir "$DATA" init --tier simple --vault "$VAULT"
```

## 4. Register tools

For each ticked item:

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness add --key "$KEY" --name "$NAME"
```

Only if the user gave a session directory and clearly allowed harvest:

```bash
--session-root "$ABS_SESSION" --harvest
```

Mount MCP only when the host can take stdio. Write `mcp/server.py` into that host’s config (templates in `adapters/`). Cursor: `.cursor/mcp.json` or the user-level `mcp.json`. Hermes: `mcp_servers.eric-memory`. If it will not mount, skip it. CLI still works. Setting a flag without writing the config file does not count as mounted.

Prefer catalog keys: `kimi` `qwen` `lingma` `workbuddy` `codebuddy` `zcode` `minimax` `trae` `comate` `openclaw` `cursor` `claude` `codex` `hermes` `grok` `other`.

## 5. Full-tier import only

Only if the user chose full tier **and** they give an existing Holograph file:

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" import holograph \
  --source "$HOLOGRAPH"
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" verify \
  --expect-query "$QUERY"
```

`$HOLOGRAPH` is an absolute path the user provides. The old file is read-only. Stop on import failure. Do not edit the old file.

## 6. Project and accept

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" sync
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" status
```

Then:

1. Tell the user to open `$VAULT` in Obsidian, starting at the home note.
2. Point at current / expired / folders / harnesses.
3. Give them the daily quest: `quests/en/daily-sync.md` (or `quests/每日同步任务.md` in Chinese).
4. Hand `skills/严格技能.md` to the active harness (project skill if it fits; otherwise remind it at the next sync).

## 7. On failure

- Python too old: stop.
- A path still contains `~`: expand, then write.
- User refuses a session directory: register the tool without `--harvest`.
- A host cannot mount MCP: note it, continue.

Report in the user’s language: data directory, vault path, registered tools, anything not mounted. Do not say “it now updates in the background.”
