#!/usr/bin/env python3
"""Stdio MCP entry. Point Cursor / Claude / other MCP hosts here."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SHIM_DIR = Path(__file__).resolve().parent
FROZEN = ROOT / ("eric-memory.exe" if os.name == "nt" else "eric-memory")
if not SRC.is_dir() and FROZEN.is_file():
    # Exact sibling executable, argument vector only, and deliberately no shell.
    os.execv(str(FROZEN), [str(FROZEN), "mcp", *sys.argv[1:]])  # noqa: S606

# Running this file puts the shim directory on sys.path[0]. A checkout folder
# named `mcp/` must not shadow the official MCP Python package of the same name.
cleaned: list[str] = []
for entry in sys.path:
    raw = Path(entry) if entry else Path.cwd()
    try:
        resolved = raw.resolve()
    except OSError:
        cleaned.append(entry)
        continue
    if resolved in {SHIM_DIR, ROOT}:
        continue
    cleaned.append(entry)
sys.path[:] = cleaned
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from eric_memory.mcp_server import main  # noqa: E402 - source checkout compatibility

if __name__ == "__main__":
    raise SystemExit(main())
