from __future__ import annotations

import unittest

from tests.helpers import SRC  # noqa: F401 — puts src/ on sys.path

from eric_memory.entities import collect_entities, is_pure_name, split_entity_tokens


class EntityTests(unittest.TestCase):
    def test_rejects_mixed_script(self) -> None:
        self.assertTrue(is_pure_name("刘昱铄"))
        self.assertTrue(is_pure_name("Qingyun"))
        self.assertTrue(is_pure_name("EC-011"))
        self.assertFalse(is_pure_name("EC-011 刘昱铄"))

    def test_splits_mixed_labels(self) -> None:
        self.assertEqual(split_entity_tokens("EC-011 刘昱铄"), ["EC-011", "刘昱铄"])

    def test_quoted_and_explicit(self) -> None:
        names = collect_entities('品牌已定名「青云未来」。', ["青云"])
        self.assertIn("青云", names)
        self.assertIn("青云未来", names)
