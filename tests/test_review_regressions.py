"""Regressions for bugs confirmed in the 2026-08-31 review probe."""

from __future__ import annotations

import os
from pathlib import Path

from eric_memory.mcp_server import (
    _as_bool,
    _as_entities,
    _parse_data_dir,
    dispatch_tool,
    handle_rpc,
)
from eric_memory.paths import PathError, expand_once, load_config
from eric_memory.store import today_utc, utc_now
from tests.helpers import TempServiceTest


class ReviewRegressionTests(TempServiceTest):
    def test_identical_content_supersede_is_idempotent(self) -> None:
        first = self.service.add("same-sentence-pointer")
        fact_id = first["fact"]["fact_id"]
        again = self.service.add("same-sentence-pointer", supersedes=fact_id)
        self.assertEqual(again["fact"]["fact_id"], fact_id)
        self.assertEqual(again["fact"]["status"], "active")
        self.assertNotIn("deprecated", again)
        self.assertEqual(self.service.store.counts()["active"], 1)

    def test_nested_folder_index_does_not_collide(self) -> None:
        parent = self.data_dir / "docs"
        child = parent / "nested"
        child.mkdir(parents=True)
        (parent / "root.txt").write_text("root", encoding="utf-8")
        (child / "child.txt").write_text("child", encoding="utf-8")
        self.service.source_approve(str(parent))
        self.service.source_approve(str(child))
        self.service.index_files(str(parent))
        report = self.service.index_files(str(child))
        self.assertEqual(report["indexed"][0]["files"], 1)
        paths = {item["path"] for item in self.service.store.list_files()}
        self.assertEqual(len(paths), 2)

    def test_mcp_boolean_is_strict_json(self) -> None:
        self.assertFalse(_as_bool("false"))
        self.assertFalse(_as_bool(None))
        self.assertTrue(_as_bool("true"))
        self.service.add("过期不应被字符串 false 翻出来。", entities=["探针"])
        rejected = dispatch_tool(
            self.service,
            "memory_search",
            {"query": "探针", "include_deprecated": "false"},
        )
        self.assertTrue(rejected["isError"])
        self.assertEqual(rejected["structuredContent"]["error"]["code"], "VALIDATION_ERROR")
        reply = dispatch_tool(
            self.service,
            "memory_search",
            {"query": "探针", "include_deprecated": False},
        )
        self.assertFalse(reply["structuredContent"]["include_deprecated"])

    def test_harness_update_keeps_approved_root(self) -> None:
        sessions = self.data_dir / "sessions"
        sessions.mkdir()
        (sessions / "note.txt").write_text("ok", encoding="utf-8")
        self.service.harness_add(
            "other",
            display_name="probe",
            session_root=str(sessions),
        )
        self.service.source_approve(str(sessions), harness_key="other")
        self.service.harness_add("other", harvest_ok=True)
        updated = self.service.harness_add("other", display_name="probe-renamed")
        self.assertTrue(updated["harness"]["session_root"])
        self.assertTrue(updated["harness"]["harvest_ok"])

    def test_missing_folder_is_skipped(self) -> None:
        gone = self.data_dir / "deleted"
        gone.mkdir()
        (gone / "x.txt").write_text("x", encoding="utf-8")
        self.service.source_approve(str(gone))
        (gone / "x.txt").unlink()
        gone.rmdir()
        report = self.service.index_files()
        self.assertEqual(report["indexed"][0]["error"], "missing")
        self.assertEqual(report["indexed"][0]["files"], 0)

    def test_as_of_uses_utc_date(self) -> None:
        self.assertEqual(today_utc(), utc_now()[:10])

    def test_add_refreshes_vault_without_sync(self) -> None:
        added = self.service.add("写入后投影必须立刻可见。", entities=["验收"])
        vault = Path(self.service.config.vault_dir) / "现行.md"
        body = vault.read_text(encoding="utf-8")
        self.assertIn("写入后投影必须立刻可见。", body)
        self.assertIn(f"#{added['fact']['fact_id']}", body)

    def test_mcp_entities_require_an_array(self) -> None:
        self.assertEqual(_as_entities("验收门,青云"), ["验收门", "青云"])
        rejected = dispatch_tool(
            self.service,
            "memory_add",
            {"content": "MCP 实体字符串必须整词保留。", "entities": "验收门,青云"},
        )
        self.assertTrue(rejected["isError"])
        self.assertEqual(rejected["structuredContent"]["error"]["code"], "VALIDATION_ERROR")
        reply = dispatch_tool(
            self.service,
            "memory_add",
            {"content": "MCP 实体数组必须整词保留。", "entities": ["验收门", "青云"]},
        )
        self.assertEqual(set(reply["structuredContent"]["fact"]["entities"]), {"验收门", "青云"})

    def test_mcp_lists_empty_resources_and_prompts(self) -> None:
        for method, key in (
            ("resources/list", "resources"),
            ("prompts/list", "prompts"),
            ("resources/templates/list", "resourceTemplates"),
        ):
            reply = handle_rpc(self.service, {"jsonrpc": "2.0", "id": 1, "method": method})
            self.assertIsNotNone(reply)
            self.assertNotIn("error", reply)
            self.assertEqual(reply["result"][key], [])

    def test_mcp_data_dir_parses_equals_and_later_flags(self) -> None:
        self.assertEqual(_parse_data_dir(["--data-dir=/tmp/mem"]), "/tmp/mem")
        self.assertEqual(_parse_data_dir(["--foo", "--data-dir", "/tmp/mem"]), "/tmp/mem")
        self.assertEqual(_parse_data_dir(["--data-dir", "/tmp/ok"]), "/tmp/ok")

    def test_expand_once_does_not_store_unexpanded_home_or_relative(self) -> None:
        tilde = expand_once("~/eric-memory-probe-dir", name="data dir")
        self.assertEqual(tilde, (Path.home() / "eric-memory-probe-dir").resolve())
        self.assertNotIn("~", str(tilde))

        fake_home = self.data_dir / "fake-home"
        fake_home.mkdir()
        previous = os.environ.get("HOME")
        os.environ["HOME"] = str(fake_home)
        try:
            from_env = expand_once("$HOME/mem-data", name="data dir")
        finally:
            if previous is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = previous
        self.assertEqual(from_env, (fake_home / "mem-data").resolve())
        self.assertNotIn("$HOME", str(from_env))
        self.assertNotIn("~", str(from_env))

        with self.assertRaises(PathError):
            expand_once("eric-memory-data", name="data dir")
        with self.assertRaises(PathError):
            expand_once("./data", name="data dir")
        if os.name == "nt" and os.environ.get("USERPROFILE"):
            win = expand_once(r"%USERPROFILE%\eric-memory-probe-dir", name="data dir")
            self.assertEqual(
                win,
                (Path(os.environ["USERPROFILE"]) / "eric-memory-probe-dir").resolve(),
            )
            self.assertNotIn("%USERPROFILE%", str(win))
        else:
            with self.assertRaises(PathError):
                expand_once(r"%USERPROFILE%\eric-memory-probe-dir", name="data dir")

        previous = os.environ.get("HOME")
        os.environ["HOME"] = str(fake_home)
        try:
            self.service.init(data_dir="$HOME/stored-data", write_repo_pointer=False)
        finally:
            if previous is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = previous
        cfg = load_config(fake_home / "stored-data")
        self.assertIsNotNone(cfg)
        for stored in (cfg.data_dir, cfg.vault_dir, cfg.db_path):
            self.assertNotIn("$HOME", stored)
            self.assertNotIn("~", stored)
            self.assertNotIn("%USERPROFILE%", stored)
            self.assertTrue(Path(stored).is_absolute())
