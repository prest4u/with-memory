"""Run a deterministic, non-overlapping share of the complete unittest suite."""

from __future__ import annotations

import argparse
import unittest
from collections.abc import Iterator
from pathlib import Path


def cases(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--list", action="store_true", dest="list_only")
    args = parser.parse_args()
    if args.count < 1 or not 0 <= args.index < args.count:
        parser.error("require count >= 1 and 0 <= index < count")
    root = Path(__file__).resolve().parents[1]
    loader = unittest.TestLoader()
    all_cases = sorted(cases(loader.discover(str(root / "tests"), top_level_dir=str(root))), key=lambda case: case.id())
    if loader.errors:
        parser.exit(1, "\n".join(loader.errors))
    ids = [case.id() for case in all_cases]
    if not ids or len(set(ids)) != len(ids):
        parser.exit(1, "test discovery must return nonempty unique test IDs\n")
    selected = all_cases[args.index :: args.count]
    if not selected:
        parser.exit(1, "empty test shard\n")
    if args.list_only:
        print("\n".join(case.id() for case in selected))
        return 0
    print(f"Shard {args.index + 1}/{args.count}: {len(selected)} of {len(all_cases)} discovered tests", flush=True)
    result = unittest.TextTestRunner(verbosity=2, failfast=True).run(unittest.TestSuite(selected))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
