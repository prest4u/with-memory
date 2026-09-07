from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from unittest.mock import patch

from eric_memory import mcp_server
from eric_memory.mcp_server import _read_message, handle_rpc
from tests.helpers import ROOT, TempServiceTest


class McpCliTests(TempServiceTest):
    def _cli(self, *args: str) -> dict:
        cmd = [
            sys.executable,
            str(ROOT / "bin" / "eric-memory"),
            "--data-dir",
            str(self.data_dir),
            "--json",
            *args,
        ]
        completed = subprocess.run(cmd, check=False, capture_output=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_chinese_cli_pipe_is_utf8_under_a_legacy_code_page(self) -> None:
        with patch.dict(os.environ, {"PYTHONIOENCODING": "cp1252"}):
            result = self._cli("add", "--content", "中文管道测试。", "--entities", "编码测试")
            self.assertEqual(result["fact"]["content"], "中文管道测试。")

    def test_cli_accepts_json_after_subcommand(self) -> None:
        added = self._cli("add", "--content", "尾部 json 旗标必须可用。", "--entities", "探针")
        cmd = [
            sys.executable,
            str(ROOT / "bin" / "eric-memory"),
            "--data-dir",
            str(self.data_dir),
            "search",
            "探针",
            "--json",
        ]
        completed = subprocess.run(cmd, check=True, capture_output=True, encoding="utf-8")
        found = json.loads(completed.stdout)
        self.assertEqual(found["facts"][0]["fact_id"], added["fact"]["fact_id"])

    def test_cli_add_search_deprecate(self) -> None:
        added = self._cli("add", "--content", "CLI 写入的现行指针。", "--entities", "验收")
        fact_id = added["fact"]["fact_id"]
        found = self._cli("search", "验收")
        self.assertEqual(found["facts"][0]["fact_id"], fact_id)
        self._cli("deprecate", str(fact_id), "--reason", "改由 MCP 覆盖")
        hidden = self._cli("search", "验收")
        self.assertEqual(hidden["facts"], [])
        shown = self._cli("search", "验收", "--include-deprecated")
        self.assertEqual(shown["facts"][0]["status"], "deprecated")

    def test_mcp_matches_cli_actions(self) -> None:
        add_rpc = handle_rpc(
            self.service,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "memory_add",
                    "arguments": {"content": "MCP 与 CLI 必须写进同一库。", "entities": ["验收"]},
                },
            },
        )
        assert add_rpc is not None
        added = add_rpc["result"]["structuredContent"]["fact"]
        search_rpc = handle_rpc(
            self.service,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "memory_search", "arguments": {"query": "验收"}},
            },
        )
        assert search_rpc is not None
        facts = search_rpc["result"]["structuredContent"]["facts"]
        self.assertEqual(facts[0]["fact_id"], added["fact_id"])

        listed = handle_rpc(self.service, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        assert listed is not None
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertTrue({"memory_add", "memory_search", "memory_deprecate", "memory_status"} <= names)

    def test_stdio_accepts_cursor_ndjson_initialize(self) -> None:
        mcp_server._stdio_framing = "content-length"
        raw = (
            b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":'
            b'{"protocolVersion":"2025-11-25","capabilities":{},'
            b'"clientInfo":{"name":"cursor-vscode","version":"1.0.0"}}}\n'
        )
        message = _read_message(io.BytesIO(raw))
        self.assertEqual(message["method"], "initialize")
        self.assertEqual(mcp_server._stdio_framing, "ndjson")
        reply = handle_rpc(self.service, message)
        assert reply is not None
        self.assertEqual(reply["id"], 0)
        self.assertIn("serverInfo", reply["result"])

    def test_bin_and_mcp_entrypoints_exist(self) -> None:
        self.assertTrue((ROOT / "bin" / "eric-memory").is_file())
        self.assertTrue((ROOT / "mcp" / "server.py").is_file())

    def test_cli_version_does_not_require_a_command(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "eric-memory"), "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("eric-memory 0.1.0", completed.stdout)
