from __future__ import annotations

import sqlite3
from pathlib import Path

from eric_memory.importer import parse_status_from_tags
from tests.helpers import TempServiceTest, make_holograph_fixture


class ImportTests(TempServiceTest):
    def test_import_preserves_tagged_deprecation_and_does_not_guess(self) -> None:
        source = make_holograph_fixture(Path(self.data_dir) / "holograph.db")
        report = self.service.import_holograph(str(source))
        self.assertEqual(report["added"], 4)
        self.assertEqual(report["policy_quarantined"], 1)
        self.assertEqual(report["deprecated"], 2)
        self.assertEqual(report["untagged_marked_active"], 1)

        current = self.service.search("青云", include_files=False)
        texts = [item["content"] for item in current["facts"]]
        self.assertTrue(any("青云未来" in text for text in texts))
        self.assertFalse(any("曾用名" in text for text in texts))

        historical = self.service.search("青云", include_deprecated=True, include_files=False)
        self.assertTrue(any("曾用名" in item["content"] for item in historical["facts"]))

        student = self.service.search("示例学员", include_files=False)
        self.assertEqual(student["facts"], [])
        quarantined = self.service.candidate_list(status="quarantined")["candidates"]
        self.assertEqual(len(quarantined), 1)
        self.assertIsNone(quarantined[0]["content"])
        self.assertIn("minor_individual_performance", quarantined[0]["policy_codes"])

        double_status = self.service.search("必须按作废导入", include_deprecated=True, include_files=False)
        self.assertEqual(double_status["facts"][0]["status"], "deprecated")
        hidden = self.service.search("必须按作废导入", include_files=False)
        self.assertEqual(hidden["facts"], [])

        again = self.service.import_holograph(str(source))
        self.assertEqual(again["added"], 0)
        self.assertEqual(again["skipped"], 5)
        self.assertEqual(again["policy_quarantined"], 0)
        self.assertEqual(again["repaired"], 0)

    def test_parse_status_deprecated_wins_over_earlier_status(self) -> None:
        self.assertEqual(parse_status_from_tags("status:ready,status:deprecated"), "deprecated")
        self.assertEqual(parse_status_from_tags("status:active"), "active")
        self.assertIsNone(parse_status_from_tags("student:示例学员"))

    def test_import_rejects_explicit_credentials_without_persisting_them(self) -> None:
        source = make_holograph_fixture(Path(self._tmpdir.name) / "holograph-secret.db")
        secret = "api_key=sk-production-secret-12345678901234567890"  # nosec-secret-test
        connection = sqlite3.connect(source)
        connection.execute(
            "INSERT INTO facts(fact_id, content, category, tags, trust_score) VALUES (99, ?, 'general', '', 0.5)",
            (secret,),
        )
        connection.commit()
        connection.close()

        report = self.service.import_holograph(str(source))
        self.assertEqual(report["policy_rejected"], 1)
        self.assertTrue(any(item["error"] == "CONTENT_REJECTED" for item in report["errors"]))
        self.assertNotIn(secret.encode(), Path(self.service.store.db_path).read_bytes())
        log = self.data_dir / "logs" / "with.jsonl"
        if log.exists():
            self.assertNotIn(secret.encode(), log.read_bytes())
