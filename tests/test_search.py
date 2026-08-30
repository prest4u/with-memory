from __future__ import annotations

from tests.helpers import TempServiceTest


class SearchTests(TempServiceTest):
    def test_chinese_like_and_default_hides_deprecated(self) -> None:
        self.service.add("刘昱铄现行课表仍按日期加主题命名。", entities=["刘昱铄"])
        old = self.service.add("刘昱铄曾用第N讲编号，这条将被作废。", entities=["刘昱铄"])
        self.service.deprecate(old["fact"]["fact_id"], reason="命名规则已改")

        current = self.service.search("刘昱铄", include_files=False)
        self.assertTrue(all(item["status"] == "active" for item in current["facts"]))
        self.assertEqual(len(current["facts"]), 1)

        leaked = self.service.verify(["刘昱铄"])
        self.assertTrue(leaked["ok"])
        self.assertEqual(leaked["reports"][0]["deprecated_leaked_into_default"], [])

        with_old = self.service.search("刘昱铄", include_deprecated=True, include_files=False)
        self.assertGreaterEqual(len(with_old["facts"]), 2)

    def test_mixed_query_tokenizes(self) -> None:
        self.service.add("天津高考 22讲 词汇手册仍是现行教辅。", tags="project:天津高考")
        hits = self.service.search("天津高考22讲", include_files=False)
        self.assertEqual(len(hits["facts"]), 1)
        self.assertEqual(hits["layer"], "like-fallback")

    def test_project_scope_filters(self) -> None:
        self.service.add("青云站点隐私合同已冻结。", tags="project:青云未来")
        self.service.add("伴学时光招生 SOP 仍用主手册。", tags="project:banxueshiguang")
        scoped = self.service.search("隐私", scope="project", project="青云未来", include_files=False)
        self.assertEqual(len(scoped["facts"]), 1)
        self.assertIn("青云", scoped["facts"][0]["content"])
