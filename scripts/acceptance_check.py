#!/usr/bin/env python3
"""Mac live gate plus portable repo check. Live half skips when those paths are absent."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = Path(__file__).resolve().parent
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from eric_memory.mcp_server import dispatch_tool
from eric_memory.service import MemoryService

from repo_check import collect_gaps

HOLOGRAPH = Path("/Users/eric/.hermes/profiles/eric/memory_store.db")
DATA = Path("/Users/eric/eric-memory-data")


def main() -> int:
    gaps = collect_gaps()
    payload: dict = {"ok": not gaps, "gaps": list(gaps), "portable": True, "live": False}

    if not (HOLOGRAPH.is_file() and DATA.is_dir() and (DATA / "memory.db").is_file()):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1

    holograph = sqlite3.connect(f"file:{HOLOGRAPH}?mode=ro", uri=True)
    h_count = holograph.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    holograph.close()
    if h_count < 565:
        gaps.append(f"holograph count {h_count} below archived import of 565")

    service = MemoryService(DATA)
    try:
        status = service.status()
        verify = service.verify(["example-name", "青云"])
        if not verify["ok"]:
            gaps.append("deprecated leaked into default search")
        cli = service.search("青云", include_files=False, limit=5)
        mcp = dispatch_tool(service, "memory_search", {"query": "青云", "limit": 5})
        cli_ids = [f["fact_id"] for f in cli["facts"]]
        mcp_ids = [f["fact_id"] for f in mcp["structuredContent"]["facts"]]
        if cli_ids != mcp_ids:
            gaps.append("cli/mcp search mismatch")
        home = Path(status["vault_dir"]) / "记忆首页.md"
        text = home.read_text(encoding="utf-8") if home.is_file() else ""
        for token in ("现行", "已过期", "资料夹", "已接工具"):
            if token not in text:
                gaps.append(f"vault home missing {token}")
        payload = {
            "ok": not gaps,
            "gaps": gaps,
            "portable": True,
            "live": True,
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
