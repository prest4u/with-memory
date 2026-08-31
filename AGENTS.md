# With.

This repository is local-first memory. The product is With. The command is `eric-memory`. Truth lives in SQLite. Obsidian is a projection.

Read [skills/严格技能.md](skills/严格技能.md) first, then call the official entry:

```text
python3 bin/eric-memory --data-dir "$HOME/eric-memory-data" search "$QUERY"
python3 bin/eric-memory --data-dir "$HOME/eric-memory-data" add --content "1–3 sentence pointer" --entities "entity"
python3 bin/eric-memory --data-dir "$HOME/eric-memory-data" deprecate ID --superseded-by NEW --reason "…"
python3 bin/eric-memory --data-dir "$HOME/eric-memory-data" sync
```

The data directory is the absolute path written at install (`$HOME/eric-memory-data` unless the owner chose another). Do not edit `memory.db` by hand. Do not treat expired rows as current. Do not walk unregistered directories.
