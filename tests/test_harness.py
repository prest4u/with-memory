from __future__ import annotations

from tests.helpers import TempServiceTest


class HarnessTests(TempServiceTest):
    def test_catalog_is_open_and_harvest_requires_approval(self) -> None:
        listed = self.service.catalog()
        keys = {item["key"] for item in listed["catalog"]}
        self.assertIn("kimi", keys)
        self.assertIn("qwen", keys)
        self.assertIn("workbuddy", keys)
        self.assertIn("other", keys)

        self.service.harness_add("cursor", mcp_mounted=True)
        with self.assertRaises(ValueError):
            self.service.harness_add("kimi", harvest_ok=True)

        other = self.service.harness_add(
            "other",
            display_name="客户自备工具",
            session_root=str(self.data_dir / "sessions"),
            harvest_ok=True,
        )
        (self.data_dir / "sessions").mkdir()
        (self.data_dir / "sessions" / "note.txt").write_text("hello", encoding="utf-8")
        synced = self.service.sync()
        harvested_keys = {item["key"] for item in synced["harvest"]}
        self.assertIn("other", harvested_keys)
        self.assertNotIn("cursor", harvested_keys)
        self.assertTrue(other["harness"]["harvest_ok"])

    def test_unregistered_source_is_not_harvested(self) -> None:
        outsider = self.data_dir / "not-registered"
        outsider.mkdir()
        (outsider / "secret.txt").write_text("no", encoding="utf-8")
        self.service.harness_add("hermes")
        synced = self.service.sync()
        self.assertEqual(synced["harvest"], [])
        paths = [item["path"] for item in self.service.store.list_files()]
        self.assertFalse(any(str(outsider) in path for path in paths))
