#!/usr/bin/env python3
"""Export pinned runtime and development requirements without the local root package."""

from __future__ import annotations

import argparse
import re
from importlib.metadata import distributions
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10 CI
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
LOCAL_ROOT_NAME = "eric-memory"


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def audit_requirements(pyproject_path: Path) -> tuple[str, ...]:
    value: dict[str, Any] = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = value.get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject.toml has no [project] table")
    runtime = project.get("dependencies")
    optional = project.get("optional-dependencies")
    development = optional.get("dev") if isinstance(optional, dict) else None
    if not isinstance(runtime, list) or not isinstance(development, list):
        raise ValueError("runtime and dev dependencies must both be arrays")
    requirements = runtime + development
    if not requirements or any(not isinstance(item, str) or not item.strip() for item in requirements):
        raise ValueError("every audit requirement must be a non-empty string")
    normalized = tuple(item.strip() for item in requirements)
    if len(set(normalized)) != len(normalized):
        raise ValueError("audit requirements contain duplicates")
    return normalized


def installed_requirements() -> tuple[str, ...]:
    installed: dict[str, tuple[str, str]] = {}
    for distribution in distributions():
        name = str(distribution.metadata.get("Name", "")).strip()
        version = str(distribution.version).strip()
        canonical = _canonical_name(name)
        if not name or not version or canonical == LOCAL_ROOT_NAME:
            continue
        existing = installed.get(canonical)
        if existing is not None and existing[1] != version:
            raise ValueError(f"multiple installed versions found for {canonical}")
        installed[canonical] = (name, version)
    if not installed:
        raise ValueError("no installed third-party distributions were found")
    return tuple(f"{installed[key][0]}=={installed[key][1]}" for key in sorted(installed))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pyproject", type=Path, default=ROOT / "pyproject.toml")
    parser.add_argument("--installed", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    requirements = installed_requirements() if args.installed else audit_requirements(args.pyproject)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    print(f"exported {len(requirements)} requirements to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
