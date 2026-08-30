#!/usr/bin/env python3
"""Read-only Mac gate. Does not write facts or touch Holograph."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from eric_memory.catalog import catalog_dicts
from eric_memory.mcp_server import TOOLS, dispatch_tool
from eric_memory.service import MemoryService

HOLOGRAPH = Path("/Users/eric/.hermes/profiles/eric/memory_store.db")
DATA = Path("/Users/eric/eric-memory-data")
PLAN_KEYS = {
    "kimi",
    "qwen",
    "lingma",
    "workbuddy",
    "codebuddy",
    "zcode",
    "minimax",
    "trae",
    "comate",
    "openclaw",
    "cursor",
    "claude",
    "codex",
    "hermes",
    "grok",
    "other",
}
NEED_MCP = {
    "memory_status",
    "memory_add",
    "memory_search",
    "memory_deprecate",
    "memory_sync",
    "memory_import",
    "memory_index_files",
    "memory_harness_add",
    "memory_harness_list",
}


def main() -> int:
    gaps: list[str] = []
    holograph = sqlite3.connect(f"file:{HOLOGRAPH}?mode=ro", uri=True)
    h_count = holograph.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    if h_count != 565:
        gaps.append(f"holograph count {h_count} != 565")

    service = MemoryService(DATA)
    try:
        status = service.status()
        verify = service.verify(["刘昱铄", "青云"])
        if not verify["ok"]:
            gaps.append("deprecated leaked into default search")
        cli = service.search("青云", include_files=False, limit=5)
        mcp = dispatch_tool(service, "memory_search", {"query": "青云", "limit": 5})
        cli_ids = [f["fact_id"] for f in cli["facts"]]
        mcp_ids = [f["fact_id"] for f in mcp["structuredContent"]["facts"]]
        if cli_ids != mcp_ids:
            gaps.append("cli/mcp search mismatch")
        keys = {item["key"] for item in catalog_dicts()}
        if PLAN_KEYS - keys:
            gaps.append(f"catalog missing {sorted(PLAN_KEYS - keys)}")
        mcp_names = {tool["name"] for tool in TOOLS}
        if NEED_MCP - mcp_names:
            gaps.append(f"mcp missing {sorted(NEED_MCP - mcp_names)}")
        home = Path(status["vault_dir"]) / "记忆首页.md"
        text = home.read_text(encoding="utf-8") if home.is_file() else ""
        for token in ("现行", "已过期", "资料夹", "已接工具"):
            if token not in text:
                gaps.append(f"vault home missing {token}")
        if not (ROOT / "AGENTS.md").is_file():
            gaps.append("AGENTS.md missing")
        mcp_local = ROOT / ".cursor" / "mcp.json"
        mcp_example = ROOT / ".cursor" / "mcp.json.example"
        has_local = mcp_local.is_file() and "eric-memory" in mcp_local.read_text(encoding="utf-8")
        has_example = mcp_example.is_file() and "eric-memory" in mcp_example.read_text(encoding="utf-8")
        if not (has_local or has_example):
            gaps.append("project mcp.json.example missing eric-memory")
        if not (ROOT / "skills" / "严格技能.md").is_file():
            gaps.append("strict skill missing")
        payload = {
            "ok": not gaps,
            "gaps": gaps,
            "holograph_facts": h_count,
            "counts": status["counts"],
            "harnesses": [h["key"] for h in status["harnesses"]],
            "verify": [
                {
                    "query": row["query"],
                    "active_hits": row["active_hits"],
                    "with_deprecated_hits": row["with_deprecated_hits"],
                    "leaked": len(row["deprecated_leaked_into_default"]),
                }
                for row in verify["reports"]
            ],
            "parity_qingyun": {"cli": cli_ids, "mcp": mcp_ids},
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
