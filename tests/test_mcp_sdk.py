from __future__ import annotations

import importlib.util
import os
import sys

from tests.helpers import ROOT, TempServiceTest


class OfficialMcpSdkTests(TempServiceTest):
    def test_official_stdio_sdk_filters_legacy_and_returns_stable_errors(self) -> None:
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("official MCP SDK is not installed in this interpreter")

        import anyio
        from mcp.client.stdio import stdio_client

        from mcp import ClientSession, StdioServerParameters

        async def exercise() -> None:
            params = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "eric_memory.mcp_server",
                    "--data-dir",
                    str(self.data_dir),
                    "--principal",
                    "legacy",
                ],
                env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUNBUFFERED": "1"},
            )
            async with (
                stdio_client(params) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                initialized = await session.initialize()
                self.assertTrue(initialized.protocol_version)
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                self.assertEqual(names, {"memory_status", "memory_search"})
                status = await session.call_tool("memory_status", {})
                self.assertFalse(status.is_error)
                self.assertEqual(status.structured_content["schema_version"], 2)
                self.assertEqual(status.structured_content["sources"], [])
                denied = await session.call_tool(
                    "memory_add",
                    {"content": "legacy must not silently write an active fact."},
                )
                self.assertTrue(denied.is_error)
                self.assertEqual(
                    denied.structured_content["error"]["code"],
                    "PERMISSION_REQUIRED",
                )

        anyio.run(exercise)

    def test_checkout_shim_from_repo_cwd_lists_tools(self) -> None:
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("official MCP SDK is not installed in this interpreter")

        import anyio
        from mcp.client.stdio import stdio_client

        from eric_memory.mcp_server import import_official_mcp
        from mcp import ClientSession, StdioServerParameters

        types, _server, stdio_server = import_official_mcp()
        self.assertTrue(types.Tool.__module__.startswith("mcp"))
        self.assertTrue(stdio_server.__module__.startswith("mcp.server"))
        self.service.harness_add("cursor", display_name="Cursor", mcp_mounted=True)

        async def exercise() -> None:
            params = StdioServerParameters(
                command=sys.executable,
                args=[
                    str(ROOT / "mcp" / "server.py"),
                    "--data-dir",
                    str(self.data_dir),
                    "--principal",
                    "cursor",
                ],
                cwd=str(ROOT),
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            async with (
                stdio_client(params) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                self.assertIn("memory_status", names)
                self.assertIn("memory_search", names)
                status = await session.call_tool("memory_status", {})
                self.assertFalse(status.is_error)
                self.assertEqual(status.structured_content["schema_version"], 2)

        anyio.run(exercise)
