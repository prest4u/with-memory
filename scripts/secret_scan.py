#!/usr/bin/env python3
"""Fail CI when tracked text contains common credential material."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bgh(?:p|o|u|s|r)_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def main() -> int:
    completed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    findings: list[str] = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        path = ROOT / raw.decode("utf-8", errors="surrogateescape")
        if path.suffix.casefold() not in TEXT_SUFFIXES or not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "nosec-secret-test" in line:
                continue
            if any(pattern.search(line) for pattern in PATTERNS):
                findings.append(f"{path.relative_to(ROOT)}:{number}")
    if findings:
        print("possible tracked credentials:")
        print("\n".join(findings))
        return 1
    print("secret scan: no tracked credential patterns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
