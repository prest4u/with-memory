[English](CONTRIBUTING.md) · [中文](CONTRIBUTING.zh-CN.md)

# Contributing

The kernel contract does not move: harnesses submit candidates; local review activates facts; invalidate rather than rewrite; write only through CLI/MCP; Obsidian is not truth; never scan an unapproved root.

The product is With. The command stays `eric-memory`.

## Develop

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/python scripts/repo_check.py
```

All normal tests use synthetic temporary databases. Never point an acceptance script, migration, or `mac_gate` at a live owner database. Do not commit `memory.db`, a host `mcp.json`, release private keys, or real harness evidence containing paths or content.

## Writes

Add a falsifying test with every behavior change. Security, migration, permission, release, and content-policy changes require independent review of the frozen candidate before publication. Preserve v1 integer IDs, command/MCP names, and existing JSON fields unless an explicit migration and recovery path is approved.

## Docs

README first screen is the brand, not a feature dump. Language switch at the top of every public page: `[English] · [中文]`. Do not write a maintainer home path into documentation.
