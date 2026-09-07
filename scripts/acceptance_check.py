#!/usr/bin/env python3
"""Portable contract checks plus opt-in, read-only checks of explicit database copies."""

from __future__ import annotations

import argparse
import json
import sqlite3
from typing import Any

from repo_check import collect_gaps

from eric_memory.mcp_server import dispatch_tool
from eric_memory.paths import require_absolute
from eric_memory.service import MemoryService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", help="explicit absolute data-directory copy to inspect read-only")
    parser.add_argument("--holograph-copy", help="explicit absolute Holograph copy to count read-only")
    parser.add_argument("--query", action="append", default=[], help="safe query used for CLI/MCP parity")
    return parser


def _holograph_count(path: str) -> int:
    source = require_absolute(path, name="holograph copy")
    connection = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
    finally:
        connection.close()


def main() -> int:
    args = _parser().parse_args()
    gaps = collect_gaps()
    payload: dict[str, Any] = {"ok": not gaps, "gaps": gaps, "portable": True, "live": False}
    if args.holograph_copy:
        payload["holograph_facts"] = _holograph_count(args.holograph_copy)
    if not args.data_dir:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1

    data_dir = require_absolute(args.data_dir, name="data directory copy")
    service = MemoryService(data_dir, mode="ro", principal="local")
    try:
        reports: list[dict[str, Any]] = []
        for query in args.query:
            cli = service.search(query, include_files=False, limit=5)
            mcp = dispatch_tool(service, "memory_search", {"query": query, "limit": 5})
            cli_ids = [item["fact_id"] for item in cli["facts"]]
            mcp_ids = [item["fact_id"] for item in mcp["structuredContent"]["facts"]]
            if cli_ids != mcp_ids:
                gaps.append(f"CLI/MCP search mismatch for query {query!r}")
            reports.append({"query": query, "cli": cli_ids, "mcp": mcp_ids})
        payload.update(
            {
                "ok": not gaps,
                "gaps": gaps,
                "live": True,
                "read_only": True,
                "schema": service.store.schema_version,
                "counts": service.store.counts(),
                "parity": reports,
            }
        )
    finally:
        service.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
