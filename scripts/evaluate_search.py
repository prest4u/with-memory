#!/usr/bin/env python3
"""Build the bilingual synthetic golden set and enforce retrieval/leakage thresholds."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from eric_memory.service import MemoryService

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLD = ROOT / "tests" / "fixtures" / "search_gold.json"


def _scope_arguments(scope: dict[str, str] | None) -> dict[str, str]:
    if not scope or scope["kind"] == "user":
        return {"scope": "user"}
    return {"scope": scope["kind"], scope["kind"]: scope["key"]}


def evaluate(dataset: str | Path = DEFAULT_GOLD) -> dict[str, Any]:
    fixture = json.loads(Path(dataset).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="with-search-gold-") as temporary:
        data_dir = Path(temporary).resolve() / "data"
        service = MemoryService.for_init(data_dir)
        service.init(data_dir=data_dir, tier="full", write_repo_pointer=False)
        ids: dict[str, int] = {}
        try:
            for item in fixture["facts"]:
                scope = item.get("scope")
                result = service.add(
                    item["content"],
                    category=item.get("category", "general"),
                    entities=item.get("entities", []),
                    **_scope_arguments(scope),
                )
                fact = result["fact"]
                ids[item["key"]] = fact["fact_id"]
                for entity, aliases in item.get("aliases", {}).items():
                    for alias in aliases:
                        service.store.add_entity_alias(entity, alias)
                if item.get("status") == "deprecated":
                    service.deprecate(fact["fact_id"], reason="golden historical fixture")

            hits = 0
            exact_hits = 0
            exact_total = 0
            deprecated_results = 0
            total_results = 0
            cross_scope_results = 0
            scoped_results = 0
            failures: list[dict[str, Any]] = []
            for case in fixture["queries"]:
                scope = case.get("scope")
                result = service.search(
                    case["query"],
                    limit=5,
                    include_files=False,
                    **_scope_arguments(scope),
                )
                result_ids = [item["fact_id"] for item in result["facts"]]
                expected_id = ids[case["expected"]]
                found = expected_id in result_ids
                hits += int(found)
                if case.get("exact_entity"):
                    exact_total += 1
                    expected = next(
                        (item for item in result["facts"] if item["fact_id"] == expected_id),
                        None,
                    )
                    exact_hits += int(
                        expected is not None and bool({"exact_entity", "entity_alias"} & set(expected["matched_by"]))
                    )
                for fact in result["facts"]:
                    total_results += 1
                    deprecated_results += int(fact["status"] == "deprecated")
                    if scope:
                        scoped_results += 1
                        cross_scope_results += int(fact["scope"] != scope)
                if not found:
                    failures.append({"query": case["query"], "expected": case["expected"], "returned_ids": result_ids})
            recall = hits / len(fixture["queries"])
            exact_recall = exact_hits / exact_total if exact_total else 1.0
            deprecated_leak = deprecated_results / total_results if total_results else 0.0
            cross_scope_leak = cross_scope_results / scoped_results if scoped_results else 0.0
            return {
                "ok": recall >= 0.90 and exact_recall == 1.0 and deprecated_leak == 0.0 and cross_scope_leak == 0.0,
                "queries": len(fixture["queries"]),
                "facts": len(fixture["facts"]),
                "recall_at_5": recall,
                "exact_entity_recall": exact_recall,
                "deprecated_leak_rate": deprecated_leak,
                "cross_scope_leak_rate": cross_scope_leak,
                "failures": failures,
            }
        finally:
            service.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(DEFAULT_GOLD))
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    report = evaluate(args.dataset)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] or args.report_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
