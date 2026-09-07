from __future__ import annotations

import io
import math

from eric_memory.mcp_server import MAX_RPC_BYTES, TOOLS, _read_message, dispatch_tool, visible_tools
from eric_memory.permissions import FACT_WRITE, MANAGE
from eric_memory.service import MemoryService
from tests.helpers import TempServiceTest


class McpSecurityTests(TempServiceTest):
    def setUp(self) -> None:
        super().setUp()
        root = self.data_dir / "mcp-source"
        root.mkdir()
        self.service.harness_add("worker")
        self.service.source_approve(str(root), harness_key="worker")
        self.worker = MemoryService(self.data_dir, principal="worker")

    def tearDown(self) -> None:
        self.worker.close()
        super().tearDown()

    def test_unknown_principal_lists_no_tools(self) -> None:
        unknown = MemoryService(self.data_dir, principal="missing-harness")
        try:
            self.assertEqual(visible_tools(unknown), [])
        finally:
            unknown.close()

    def test_tool_discovery_is_capability_filtered(self) -> None:
        names = {item["name"] for item in visible_tools(self.worker)}
        self.assertEqual(
            names,
            {
                "memory_status",
                "memory_search",
                "memory_candidate_add",
                "memory_candidate_list",
                "memory_source_list",
                "memory_harvest_begin",
                "memory_harvest_complete",
            },
        )
        self.service.harness_grant("worker", FACT_WRITE)
        names = {item["name"] for item in visible_tools(self.worker)}
        self.assertIn("memory_add", names)
        self.assertIn("memory_deprecate", names)
        self.assertNotIn("memory_import", names)
        self.assertNotIn("memory_harness_add", names)

    def test_all_tool_roots_are_closed_json_schemas(self) -> None:
        for tool in TOOLS:
            self.assertEqual(tool["inputSchema"]["type"], "object", tool["name"])
            self.assertIs(tool["inputSchema"]["additionalProperties"], False, tool["name"])
            self.assertEqual(tool["outputSchema"]["type"], "object", tool["name"])
            self.assertIs(tool["outputSchema"]["additionalProperties"], False, tool["name"])

    def test_manage_grant_lists_harnesses_without_advertising_local_mutations(self) -> None:
        self.service.harness_grant("worker", MANAGE)
        names = {item["name"] for item in visible_tools(self.worker)}
        local_names = {item["name"] for item in visible_tools(self.service)}
        self.assertIn("memory_harness_list", names)
        self.assertFalse(dispatch_tool(self.worker, "memory_harness_list", {})["isError"])
        for name, arguments in (
            ("memory_index_files", {}),
            ("memory_import", {"source": str(self.data_dir)}),
            ("memory_harness_add", {"key": "unapproved"}),
        ):
            with self.subTest(tool=name):
                self.assertNotIn(name, names)
                self.assertIn(name, local_names)
                result = dispatch_tool(self.worker, name, arguments)
                self.assertEqual(result["structuredContent"]["error"]["code"], "PERMISSION_REQUIRED")

    def test_strict_types_bounds_formats_and_extra_fields(self) -> None:
        cases = [
            ("memory_search", {"query": "x", "include_deprecated": "false"}),
            ("memory_search", {"query": "x", "limit": -1}),
            ("memory_search", {"query": "x", "unexpected": True}),
            (
                "memory_candidate_add",
                {
                    "content": "A valid candidate body.",
                    "submission_uid": "retry-key",
                    "source_uid": "not-a-uuid",
                    "source_locator": "note.md",
                },
            ),
        ]
        for name, arguments in cases:
            result = dispatch_tool(self.worker, name, arguments)
            self.assertTrue(result["isError"], (name, arguments))
            self.assertEqual(result["structuredContent"]["error"]["code"], "VALIDATION_ERROR")

        empty_array = dispatch_tool(self.worker, "memory_status", [])
        self.assertTrue(empty_array["isError"])
        self.assertEqual(empty_array["structuredContent"]["error"]["code"], "VALIDATION_ERROR")

        bad_date = dispatch_tool(
            self.service,
            "memory_add",
            {"content": "Invalid date must be rejected.", "as_of": "2026-02-30"},
        )
        self.assertEqual(bad_date["structuredContent"]["error"]["code"], "VALIDATION_ERROR")
        non_finite = dispatch_tool(
            self.service,
            "memory_add",
            {"content": "Non-finite trust must be rejected.", "trust": math.nan},
        )
        self.assertEqual(non_finite["structuredContent"]["error"]["code"], "VALIDATION_ERROR")

    def test_error_codes_are_stable_and_legacy_writes_never_fall_through(self) -> None:
        missing = dispatch_tool(self.service, "memory_history", {"fact_id": 999_999})
        self.assertTrue(missing["isError"])
        self.assertEqual(missing["structuredContent"]["error"]["code"], "NOT_FOUND")

        legacy = MemoryService(self.data_dir, principal="legacy")
        try:
            denied = dispatch_tool(
                legacy,
                "memory_add",
                {"content": "Legacy must return a permission migration error."},
            )
            self.assertTrue(denied["isError"])
            self.assertEqual(denied["structuredContent"]["error"]["code"], "PERMISSION_REQUIRED")
        finally:
            legacy.close()

    def test_success_and_error_structured_content_validate_against_published_schema(self) -> None:
        from jsonschema import validate

        status = dispatch_tool(self.worker, "memory_status", {})
        validate(
            status["structuredContent"],
            next(t for t in TOOLS if t["name"] == "memory_status")["outputSchema"],
        )
        search = dispatch_tool(self.worker, "memory_search", {"query": "none"})
        validate(
            search["structuredContent"],
            next(t for t in TOOLS if t["name"] == "memory_search")["outputSchema"],
        )
        error = dispatch_tool(self.worker, "memory_search", {"query": "x", "limit": 0})
        validate(
            error["structuredContent"],
            next(t for t in TOOLS if t["name"] == "memory_search")["outputSchema"],
        )

    def test_legacy_framing_rejects_oversized_requests(self) -> None:
        payload = b"{" + (b" " * MAX_RPC_BYTES) + b"}\n"
        with self.assertRaises(ValueError):
            _read_message(io.BytesIO(payload))
