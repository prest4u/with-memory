from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT  # noqa: F401 - inserts src onto sys.path

# isort: split

from eric_memory.errors import PermissionRequiredError
from eric_memory.mcp_server import dispatch_tool, import_official_mcp, visible_tools
from eric_memory.service import MemoryService
from tests.test_migration_backup_purge import make_v1_fixture


class SchemaV1CompatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name).resolve() / "data"
        make_v1_fixture(self.data_dir)
        self.cursor = MemoryService(self.data_dir, mode="ro", principal="cursor")

    def tearDown(self) -> None:
        self.cursor.close()
        self.temporary.cleanup()

    def test_v1_status_and_search_work_for_named_principal(self) -> None:
        status = self.cursor.status()
        self.assertEqual(status["schema_version"], 1)
        found = self.cursor.search("Legacy", include_files=True)
        self.assertEqual(found["facts"][0]["fact_id"], 7)
        self.assertNotIn("files", found)

    def test_v1_mcp_lists_status_and_search_and_rejects_writes(self) -> None:
        names = {tool["name"] for tool in visible_tools(self.cursor)}
        self.assertEqual(names, {"memory_status", "memory_search"})
        listed = dispatch_tool(self.cursor, "memory_search", {"query": "Legacy"})
        self.assertFalse(listed["isError"])
        self.assertEqual(listed["structuredContent"]["facts"][0]["fact_id"], 7)
        denied = dispatch_tool(
            self.cursor,
            "memory_add",
            {"content": "v1 named principals must not write."},
        )
        self.assertTrue(denied["isError"])
        self.assertEqual(denied["structuredContent"]["error"]["code"], "PERMISSION_REQUIRED")

    def test_v1_write_capabilities_stay_blocked(self) -> None:
        writable = MemoryService(self.data_dir, mode="rw-existing", principal="cursor")
        try:
            with self.assertRaises(PermissionRequiredError):
                writable.add("must stay blocked until migration")
        finally:
            writable.close()

    def test_v1_cli_source_list_returns_migration_error_without_traceback(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/eric-memory"),
                "--data-dir",
                str(self.data_dir),
                "--json",
                "source",
                "list",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "MIGRATION_REQUIRED")
        self.assertNotIn("Traceback", result.stderr)

    def test_local_history_remains_available_without_granting_harness_history(self) -> None:
        local = MemoryService(self.data_dir, mode="ro")
        try:
            self.assertTrue(local.history(9))
            self.assertTrue(local.search("Legacy", include_deprecated=True)["facts"])
        finally:
            local.close()
        with self.assertRaises(PermissionRequiredError):
            self.cursor.search("Legacy", include_deprecated=True)

    def test_nonempty_v1_search_matches_published_schema(self) -> None:
        from jsonschema import Draft202012Validator, FormatChecker

        result = dispatch_tool(self.cursor, "memory_search", {"query": "Legacy"})
        schema = next(tool["outputSchema"] for tool in visible_tools(self.cursor) if tool["name"] == "memory_search")
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(result["structuredContent"])

    def test_real_stdio_v1_server_opens_readonly_database(self) -> None:
        import anyio

        import_official_mcp()
        from mcp.client.stdio import stdio_client

        from mcp import ClientSession, StdioServerParameters

        database = self.data_dir / "memory.db"
        database.chmod(0o444)

        async def exercise() -> None:
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(ROOT / "mcp/server.py"), "--data-dir", str(self.data_dir), "--principal", "kimi"],
                cwd=str(ROOT),
            )
            async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool("memory_search", {"query": "Legacy"})
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content["facts"][0]["fact_id"], 7)

        try:
            anyio.run(exercise)
            self.assertFalse(Path(str(database) + "-wal").exists())
        finally:
            database.chmod(0o600)
