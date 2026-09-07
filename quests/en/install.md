[English](install.md) · [中文](../安装任务.md)

# Quest: install With. safely

Read `skills/严格技能.md` first. Do not use admin rights, modify `PATH`, install a daemon or scheduler, connect a cloud service, or touch an existing real database without exact-path and operation-specific approval.

## Decisions

Ask for the absolute data directory, optional Obsidian Vault, each harness/key, and every explicitly consented source with owner/include/exclude/type/size rules. Do not search for unknown paths. v1 has one unified mode; do not ask simple versus full.

For a source checkout:

```bash
python3 -m venv "$REPO/.venv"
"$REPO/.venv/bin/python" -m pip install --upgrade "pip==26.2.1"
"$REPO/.venv/bin/python" -m pip install "$REPO"
"$ERIC_MEMORY" --data-dir "$DATA" init --no-obsidian
```

Use an enabled absolute Vault instead of `--no-obsidian` when requested. Stop if the target already has a database; run read-only `status` and `migrate --plan` instead.

Register each identity, then approve each source separately:

```bash
"$ERIC_MEMORY" --data-dir "$DATA" harness add --key "$KEY" --name "$NAME" --mcp-mounted
"$ERIC_MEMORY" --data-dir "$DATA" source approve "$SOURCE" --harness "$KEY"
```

Never use compatibility `index-files` or `harness add --harvest` to bypass consent. Configure stdio with `--principal KEY`; never configure HTTP.

For existing data, plan read-only, create and verify a backup, ask again, and migrate only a copy during RC. Finish with `doctor`, `backup create`, and a pending manual candidate. Let the user decide it in `review`; do not auto-accept it.

Report absolute paths, principals, approved source UIDs, MCP gaps, backup location, and doctor warnings. Do not claim background synchronization.
