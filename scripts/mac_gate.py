#!/usr/bin/env python3
"""Mac acceptance gate for the first edition. Does not touch Holograph except as a read-only import."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from eric_memory.mcp_server import handle_rpc
from eric_memory.paths import expand_once
from eric_memory.service import MemoryService

HOLOGRAPH = Path("/Users/eric/.hermes/profiles/eric/memory_store.db")
DATA = Path("/Users/eric/eric-memory-data")
INDEX_SAMPLE = ROOT / "vault-template"


def _rpc(service: MemoryService, name: str, arguments: dict | None = None) -> dict:
    reply = handle_rpc(
        service,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )
    assert reply is not None
    result = reply["result"]
    if result.get("isError"):
        raise RuntimeError(result["content"][0]["text"])
    return result["structuredContent"]


def main() -> int:
    data_dir = expand_once(DATA, name="gate data dir")
    holograph = expand_once(HOLOGRAPH, name="holograph db")
    service = MemoryService(data_dir)
    try:
        init = service.init(data_dir=data_dir, tier="full", folders=[str(INDEX_SAMPLE)])
        imported = service.import_holograph(str(holograph))
        service.harness_add("cursor", mcp_mounted=True, notes="Mac gate: CLI / this Cursor session")
        service.harness_add("hermes", mcp_mounted=True, notes="Mac gate: MCP as second harness")
        cli_add = service.add(
            "Mac 验收：Cursor CLI 写入一条现行指针，路径在 /Users/eric/Documents/eric-memory。",
            entities=["验收门"],
            category="workflow",
            actor="cursor",
        )
        mcp_add = _rpc(
            service,
            "memory_add",
            {
                "content": "Mac 验收：Hermes/MCP 写入第二条指针，随后作废 Cursor 那条测试句。",
                "entities": ["验收门"],
                "supersedes": cli_add["fact"]["fact_id"],
            },
        )
        search = _rpc(service, "memory_search", {"query": "验收门"})
        verify = service.verify(["example-name", "青云", "验收门"])
        indexed = service.index_files(str(INDEX_SAMPLE))
        synced = service.sync(actor="mac-gate")
        vault = Path(synced["vault_dir"])
        home = (vault / "记忆首页.md").read_text(encoding="utf-8")
        checks = {
            "init": init,
            "import_added": imported["added"],
            "import_skipped": imported["skipped"],
            "counts": service.store.counts(),
            "verify": verify,
            "mcp_search_ids": [f["fact_id"] for f in search["facts"]],
            "mcp_current": mcp_add["fact"]["fact_id"],
            "indexed": indexed,
            "vault_home_has_sections": all(
                token in home for token in ("现行", "已过期", "已接工具", "资料夹")
            ),
            "harnesses": [h["key"] for h in service.harness_list()["harnesses"]],
        }
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        if imported["added"] + imported["skipped"] < 500:
            raise SystemExit("import did not reach ~565 holograph facts")
        if not verify["ok"]:
            raise SystemExit("deprecated facts leaked into default search")
        if mcp_add["fact"]["fact_id"] not in checks["mcp_search_ids"]:
            raise SystemExit("second harness could not see its own write")
        if cli_add["fact"]["fact_id"] in checks["mcp_search_ids"]:
            if any(f["fact_id"] == cli_add["fact"]["fact_id"] and f["status"] == "active" for f in search["facts"]):
                raise SystemExit("superseded CLI fact still active in default search")
        if not checks["vault_home_has_sections"]:
            raise SystemExit("Obsidian home missing required sections")
        print("MAC_GATE_OK")
        return 0
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
