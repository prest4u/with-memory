from __future__ import annotations

import unittest

from tests.helpers import SRC  # noqa: F401 — puts src/ on sys.path

from eric_memory.entities import collect_entities, is_pure_name, split_entity_tokens


class EntityTests(unittest.TestCase):
    def test_rejects_mixed_script(self) -> None:
        self.assertTrue(is_pure_name("示例学员"))
        self.assertTrue(is_pure_name("Qingyun"))
        self.assertTrue(is_pure_name("STU-0001"))
        self.assertFalse(is_pure_name("STU-0001 示例学员"))

    def test_splits_mixed_labels(self) -> None:
        self.assertEqual(split_entity_tokens("STU-0001 示例学员"), ["STU-0001", "示例学员"])

    def test_quoted_and_explicit(self) -> None:
        names = collect_entities('品牌已定名「青云未来」。', ["青云"])
        self.assertIn("青云", names)
        self.assertIn("青云未来", names)
