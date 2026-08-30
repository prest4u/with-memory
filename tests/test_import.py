from __future__ import annotations

from pathlib import Path

from eric_memory.importer import parse_status_from_tags
from tests.helpers import TempServiceTest, make_holograph_fixture


class ImportTests(TempServiceTest):
    def test_import_preserves_tagged_deprecation_and_does_not_guess(self) -> None:
        source = make_holograph_fixture(Path(self.data_dir) / "holograph.db")
        report = self.service.import_holograph(str(source))
        self.assertEqual(report["added"], 5)
        self.assertEqual(report["deprecated"], 2)
        self.assertEqual(report["untagged_marked_active"], 2)

        current = self.service.search("青云", include_files=False)
        texts = [item["content"] for item in current["facts"]]
        self.assertTrue(any("青云未来" in text for text in texts))
        self.assertFalse(any("曾用名" in text for text in texts))

        historical = self.service.search("青云", include_deprecated=True, include_files=False)
        self.assertTrue(any("曾用名" in item["content"] for item in historical["facts"]))

        liu = self.service.search("刘昱铄", include_files=False)
        self.assertGreaterEqual(len(liu["facts"]), 1)
        self.assertTrue(all(item["status"] == "active" for item in liu["facts"]))

        double_status = self.service.search("必须按作废导入", include_deprecated=True, include_files=False)
        self.assertEqual(double_status["facts"][0]["status"], "deprecated")
        hidden = self.service.search("必须按作废导入", include_files=False)
        self.assertEqual(hidden["facts"], [])

        again = self.service.import_holograph(str(source))
        self.assertEqual(again["added"], 0)
        self.assertEqual(again["skipped"], 5)
        self.assertEqual(again["repaired"], 0)

    def test_parse_status_deprecated_wins_over_earlier_status(self) -> None:
        self.assertEqual(parse_status_from_tags("status:ready,status:deprecated"), "deprecated")
        self.assertEqual(parse_status_from_tags("status:active"), "active")
        self.assertIsNone(parse_status_from_tags("student:刘昱铄"))
