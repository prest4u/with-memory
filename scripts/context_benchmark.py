"""Reproducible synthetic output benchmark; reports bytes, never billing tokens."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eric_memory.context_store import encoded_size  # noqa: E402
from eric_memory.mcp_server import visible_tools  # noqa: E402
from eric_memory.permissions import CONTEXT_READ, CONTEXT_WRITE  # noqa: E402
from eric_memory.scopes import ScopeSpec  # noqa: E402
from eric_memory.service import MemoryService  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="with-context-benchmark-") as directory:
        base = Path(directory).resolve()
        source_dir = base / "input"
        source_dir.mkdir()
        text = "\n".join(f"INFO job={i} completed successfully" for i in range(10000))
        text += "\nERROR Orchid: deployment failed because widget_version is missing.\n"
        (source_dir / "build.log").write_text(text, encoding="utf-8")
        memory = MemoryService.for_init(base / "data")
        try:
            memory.init(data_dir=base / "data", obsidian_enabled=False, write_repo_pointer=False)
            memory.harness_add("example", display_name="Example client")
            source = memory.source_approve(str(source_dir), harness_key="example")["source"]
            memory.context.enable("demo", source["source_uid"], allow_content_storage=True)
            for cap in (CONTEXT_READ, CONTEXT_WRITE):
                memory.store.grant("example", cap, scope=ScopeSpec("project", "demo"))
            client = MemoryService(base / "data", mode="ro", principal="example")
            try:
                receipt = client.context.index("demo", source["source_uid"], "build.log")
                result = client.context.recall("Orchid widget_version", "demo", max_bytes=2048)
                found = any("ERROR Orchid" in item["content"] for item in result["results"])
                if not found:
                    raise AssertionError("the required failure detail was not retrieved")
                raw_bytes = len(text.encode("utf-8"))
                returned = encoded_size(receipt) + encoded_size(result)
                native = "\n".join(line for line in text.splitlines() if "ERROR" in line)
                report = {
                    "fixture": "synthetic 10000-line build log",
                    "correct_failure_retrieved": found,
                    "raw_bytes": raw_bytes,
                    "receipt_and_recall_json_bytes": returned,
                    "payload_reduction_percent": round((1 - returned / raw_bytes) * 100, 2),
                    "native_filter_text_bytes": len(native.encode("utf-8")),
                    "optional_tool_schema_json_bytes": encoded_size(
                        [
                            tool
                            for tool in visible_tools(client)
                            if tool["name"] in {"memory_recall", "memory_context_read", "memory_context_index"}
                        ]
                    ),
                    "limitation": "Payload bytes only. Includes index receipt and recall. Excludes requests, prompts, "
                    "MCP framing, model tokens and task quality. Native filtering is a separate baseline.",
                }
                print(json.dumps(report, ensure_ascii=False, indent=2))
            finally:
                client.close()
        finally:
            memory.close()


if __name__ == "__main__":
    main()
