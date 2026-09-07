#!/usr/bin/env python3
"""Publish approved assets with explicit prerelease routing."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


def release_command(version: str, directory: Path) -> list[str]:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
        raise ValueError("release version must be an exact semantic version")
    assets = sorted(
        str(path.resolve())
        for path in directory.iterdir()
        if path.is_file()
        and not path.is_symlink()
        and (
            path.name == "SHA256SUMS"
            or path.name.endswith((".zip", "-manifest.json", "-manifest.json.sig", "-sbom.cdx.json"))
        )
    )
    if not assets:
        raise ValueError("no approved release assets were found")
    command = [
        "gh",
        "release",
        "create",
        f"v{version}",
        *assets,
        "--verify-tag",
        "--title",
        f"With. v{version}",
        "--generate-notes",
    ]
    if "-" in version:
        command.extend(("--prerelease", "--latest=false"))
    return command


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    args = parser.parse_args()
    subprocess.run(release_command(args.version, args.release_dir), check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
