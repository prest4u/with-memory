#!/usr/bin/env python3
"""Fail closed unless version, tag, commit, and embedded update trust root agree."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

import tomllib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?")


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    version = args.version.removeprefix("v")
    if not VERSION_RE.fullmatch(version):
        raise SystemExit("release version must be strict semantic versioning")
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        raise SystemExit("source commit must be a full lowercase SHA-1")

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = str(pyproject["project"]["version"])
    init_text = (ROOT / "src" / "eric_memory" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', init_text, flags=re.MULTILINE)
    if package_version != version or match is None or match.group(1) != version:
        raise SystemExit("tag, pyproject.toml, and eric_memory.__version__ must match exactly")
    if _git("rev-parse", "HEAD") != args.source_commit:
        raise SystemExit("checked-out commit does not match the frozen source commit")
    try:
        tagged = _git("rev-list", "-n", "1", f"v{version}")
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"release tag v{version} is missing") from exc
    if tagged != args.source_commit:
        raise SystemExit("release tag does not identify the frozen source commit")

    public_key_path = ROOT / "src" / "eric_memory" / "resources" / "release-public-key.pem"
    if not public_key_path.is_file():
        raise SystemExit("embedded Ed25519 release trust root is missing")
    public_key = serialization.load_pem_public_key(public_key_path.read_bytes())
    if not isinstance(public_key, Ed25519PublicKey):
        raise SystemExit("embedded release trust root is not Ed25519")
    fingerprint = hashlib.sha256(public_key_path.read_bytes()).hexdigest()
    print(f"release identity verified: v{version} {args.source_commit} trust-root-sha256={fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
