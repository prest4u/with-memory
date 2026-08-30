#!/usr/bin/env python3
"""Stdio MCP entry. Point Cursor / Claude / other MCP hosts here."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from eric_memory.mcp_server import main

if __name__ == "__main__":
    raise SystemExit(main())
