from __future__ import annotations

import json
import os
import sys

from eric_memory.permissions import CONTEXT_READ, CONTEXT_WRITE, FACT_SEARCH
from eric_memory.scopes import ScopeSpec
from tests.helpers import ROOT, TempServiceTest


class ContextMcpTests(TempServiceTest):
    def test_shared_stdio_contract_for_harness_principals_and_restart(self) -> None:
        import anyio
        from mcp.client.stdio import stdio_client

        from mcp import ClientSession, StdioServerParameters

        project = "portable"
        root = (self.data_dir.parent / "Inputs 中文 with spaces").resolve()
        root.mkdir()
        (root / "build.log").write_text(
            "\n".join(f"INFO item={i} completed" for i in range(3000)) + "\nERROR Orchid failed at module Widget\n",
            encoding="utf-8",
            newline="\n",
        )
        self.service.add("Orchid uses the Widget module.", scope="project", project=project)
        source = self.service.source_approve(str(root), harness_key="codex")["source"]
        self.service.context.enable(project, source["source_uid"], allow_content_storage=True)
        principals = ("codex", "cursor", "kimi", "claude", "hermes", "qwen", "grok", "custom-cli")
        for key in principals:
            self.service.harness_add(key, display_name=key)
            for cap in (CONTEXT_READ, FACT_SEARCH):
                self.service.store.grant(key, cap, scope=ScopeSpec("project", project))
        self.service.store.grant("codex", CONTEXT_WRITE, scope=ScopeSpec("project", project))

        async def exercise() -> None:
            artifact_uid = ""
            for key in (*principals, "codex"):
                params = StdioServerParameters(
                    command=sys.executable,
                    args=[str(ROOT / "mcp/server.py"), "--data-dir", str(self.data_dir), "--principal", key],
                    cwd=str(root),
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )
                async with (
                    stdio_client(params) as (reader, writer),
                    ClientSession(reader, writer) as client,
                ):
                    await client.initialize()
                    listed = await client.list_tools()
                    names = {tool.name for tool in listed.tools}
                    self.assertIn("memory_recall", names)
                    self.assertIn("memory_context_read", names)
                    self.assertEqual("memory_context_index" in names, key == "codex")
                    if not artifact_uid:
                        indexed = await client.call_tool(
                            "memory_context_index",
                            {
                                "project": project,
                                "source_uid": source["source_uid"],
                                "source_locator": "build.log",
                            },
                        )
                        self.assertFalse(indexed.is_error, str(indexed))
                        artifact_uid = indexed.structured_content["artifact_uid"]
                    found = await client.call_tool(
                        "memory_recall",
                        {
                            "project": project,
                            "query": "Orchid Widget",
                            "max_bytes": 2048,
                        },
                    )
                    self.assertFalse(found.is_error, str(found))
                    payload = found.structured_content
                    self.assertEqual({item["kind"] for item in payload["results"]}, {"fact", "context"})
                    excerpts = [item["content"] for item in payload["results"] if item["kind"] == "context"]
                    self.assertTrue(any("ERROR Orchid failed" in text for text in excerpts))
                    self.assertLessEqual(len(found.content[0].text.encode("utf-8")), 2048)
                    self.assertEqual(json.loads(found.content[0].text), payload)
                    exact = await client.call_tool(
                        "memory_context_read",
                        {
                            "project": project,
                            "artifact_uid": artifact_uid,
                            "start_line": 3001,
                        },
                    )
                    self.assertFalse(exact.is_error, str(exact))
                    self.assertEqual(
                        exact.structured_content["results"][0]["content"], "ERROR Orchid failed at module Widget\n"
                    )
                    invalid = await client.call_tool(
                        "memory_recall",
                        {
                            "project": project,
                            "query": "Orchid",
                            "max_bytes": -1,
                        },
                    )
                    self.assertTrue(invalid.is_error)
                    denied = await client.call_tool("memory_recall", {"project": "private", "query": "Orchid"})
                    self.assertTrue(denied.is_error)
                    self.assertEqual(denied.structured_content["error"]["code"], "PERMISSION_REQUIRED")

        anyio.run(exercise)
