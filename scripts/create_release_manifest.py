#!/usr/bin/env python3
"""Create the canonical signed-manifest payload for exactly four native artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

PLATFORMS = ("macos-arm64", "macos-x86_64", "windows-x86_64", "linux-x86_64")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--schema-version", type=int, default=2)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("archives", nargs="+")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        raise SystemExit("source commit must be a full lowercase SHA-1")
    expected = {f"with-{args.version}-{tag}.zip" for tag in PLATFORMS}
    paths = [Path(item).resolve() for item in args.archives]
    actual = {path.name for path in paths}
    if actual != expected or len(paths) != len(expected):
        raise SystemExit(f"release requires exactly {sorted(expected)}, got {sorted(actual)}")
    trust_root = Path(__file__).resolve().parents[1] / "src" / "eric_memory" / "resources" / "release-public-key.pem"
    if not trust_root.is_file():
        raise SystemExit("release trust root is missing; refusing to create a publishable manifest")
    manifest = {
        "format": "with-release-v1",
        "version": args.version,
        "schema_version": args.schema_version,
        "assets": {
            path.name: {"sha256": _sha256(path), "size": path.stat().st_size}
            for path in sorted(paths, key=lambda item: item.name)
        },
        "source_commit": args.source_commit,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
