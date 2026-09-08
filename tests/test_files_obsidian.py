from __future__ import annotations

from pathlib import Path

from eric_memory.errors import ValidationError
from tests.helpers import TempServiceTest


class FilesObsidianTests(TempServiceTest):
    def test_legacy_folder_registration_cannot_grant_source_consent(self) -> None:
        folder = self.data_dir / "unapproved"
        folder.mkdir()
        registered = self.service.add_folder(str(folder))
        self.assertFalse(registered["approved"])
        self.assertIsNone(self.service.store.source_by_root(folder))
        with self.assertRaises(ValidationError):
            self.service.index_files(str(folder))

    def test_index_files_returns_paths_not_bodies(self) -> None:
        folder = self.data_dir / "docs"
        folder.mkdir()
        sample = folder / "brief.md"
        sample.write_text("这是正文，不应变成事实。", encoding="utf-8")
        self.service.source_approve(str(folder))
        report = self.service.index_files(str(folder))
        self.assertEqual(report["indexed"][0]["files"], 1)
        hits = self.service.search("brief", include_files=True)
        self.assertEqual(hits["files"][0]["path"], str(sample.resolve()))
        self.assertFalse(any("不应变成事实" in fact["content"] for fact in hits["facts"]))

    def test_vault_pages_exist_after_sync(self) -> None:
        added = self.service.add("现行条目：客户只用 Obsidian 看记忆。", entities=["Obsidian"])
        before_sync = (Path(self.service.config.vault_dir) / "现行.md").read_text(encoding="utf-8")
        self.assertIn("客户只用 Obsidian", before_sync)
        self.assertIn(f"#{added['fact']['fact_id']}", before_sync)
        old = self.service.add("旧说法：还要做一个 App。")
        self.service.deprecate(old["fact"]["fact_id"], reason="产品决定不做 App")
        self.service.harness_add("cursor", mcp_mounted=True)
        synced = self.service.sync()
        vault = Path(synced["vault_dir"])
        home = (vault / "记忆首页.md").read_text(encoding="utf-8")
        active = (vault / "现行.md").read_text(encoding="utf-8")
        deprecated = (vault / "已过期.md").read_text(encoding="utf-8")
        tools = (vault / "已接工具.md").read_text(encoding="utf-8")
        self.assertIn("现行事实", home)
        self.assertIn("已过期", home)
        self.assertIn("已接工具", home)
        self.assertIn("客户只用 Obsidian", active)
        self.assertNotIn("还要做一个 App", deprecated)
        self.assertIn(f"#{old['fact']['fact_id']}", deprecated)
        self.assertIn("投影不重复已过期正文", deprecated)
        self.assertIn("Cursor", tools)
        self.assertIn("现行索引文件", home)
        self.assertIn("已点头的资料夹", home)
