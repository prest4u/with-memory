from __future__ import annotations

from tests.helpers import TempServiceTest


class StoreTests(TempServiceTest):
    def test_add_search_deprecate_contract(self) -> None:
        added = self.service.add(
            "青云未来网站仍按备案复核判定 needs_review。",
            category="project",
            entities=["青云", "青云未来"],
        )
        fact_id = added["fact"]["fact_id"]
        hits = self.service.search("青云", include_files=False)
        self.assertEqual(hits["layer"], "entity")
        self.assertEqual(len(hits["facts"]), 1)
        self.assertEqual(hits["facts"][0]["fact_id"], fact_id)

        replacement = self.service.add(
            "青云未来备案已通过，现行状态是 live。",
            entities=["青云未来"],
            supersedes=fact_id,
        )
        default = self.service.search("青云", include_files=False)
        self.assertEqual(len(default["facts"]), 1)
        self.assertEqual(default["facts"][0]["status"], "active")
        self.assertEqual(default["facts"][0]["fact_id"], replacement["fact"]["fact_id"])

        historical = self.service.search("青云", include_deprecated=True, include_files=False)
        statuses = {item["fact_id"]: item["status"] for item in historical["facts"]}
        self.assertEqual(statuses[fact_id], "deprecated")
        self.assertEqual(historical["facts"][0]["status"], "active")

    def test_duplicate_active_content_is_idempotent(self) -> None:
        first = self.service.add("同一句话只保留一条现行。")
        second = self.service.add("同一句话只保留一条现行。")
        self.assertEqual(first["fact"]["fact_id"], second["fact"]["fact_id"])
        self.assertEqual(self.service.store.counts()["active"], 1)

    def test_empty_content_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.add("   ")

    def test_cannot_supersede_self(self) -> None:
        fact = self.service.add("占位。")["fact"]
        with self.assertRaises(ValueError):
            self.service.deprecate(fact["fact_id"], superseded_by=fact["fact_id"])
