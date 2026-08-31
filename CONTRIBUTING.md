[English](CONTRIBUTING.md) · [中文](CONTRIBUTING.zh-CN.md)

# Contributing

The kernel contract does not move: invalidate, do not delete; write only through CLI or MCP; Obsidian is not the source of truth; do not scan the whole disk by default; do not swap in Mem0, Graphiti, Cognee, or Supermemory.

The product is With. The command stays `eric-memory`.

## Develop

```bash
python3 -m unittest discover -s tests -v
python3 scripts/repo_check.py
python3 bin/eric-memory --version
```

Run `python3 scripts/acceptance_check.py` only when a live data directory already exists on the machine. Do not commit `memory.db` or a host `mcp.json`.

## Writes

Add a test before you change behavior. Regressions go in `tests/test_review_regressions.py`. English is the public default. Keep the Chinese quests in `quests/` for mainland paste-in installs.

## Docs

README first screen is the brand, not a feature dump. Language switch at the top of every public page: `[English] · [中文]`. Do not write a maintainer home path into documentation.
