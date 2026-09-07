from __future__ import annotations

import sys
import unittest

from tests.helpers import ROOT

SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from benchmark import run_benchmark  # noqa: E402 - local release-gate script
from evaluate_search import evaluate  # noqa: E402 - local release-gate script


class SearchQualityGateTests(unittest.TestCase):
    def test_bilingual_golden_retrieval_and_leakage_gates(self) -> None:
        report = evaluate()
        self.assertGreaterEqual(report["recall_at_5"], 0.90, report)
        self.assertEqual(report["exact_entity_recall"], 1.0, report)
        self.assertEqual(report["deprecated_leak_rate"], 0.0, report)
        self.assertEqual(report["cross_scope_leak_rate"], 0.0, report)

    def test_performance_gate_smoke_exercises_unchanged_scan_without_rehash(self) -> None:
        report = run_benchmark(facts=200, files=100, protocol=False)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["scan"]["unchanged_files"], 100)
        self.assertEqual(report["scan"]["changed_files"], 0)
