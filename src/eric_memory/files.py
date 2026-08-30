"""Index user-approved folders. Facts store pointers, never file bodies."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .paths import require_absolute

SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".idea",
    ".cursor",
    ".DS_Store",
}

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_FILES = 5000


def _should_skip_dir(name: str) -> bool:
    return name in SKIP_DIR_NAMES or name.startswith(".")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def walk_folder(folder: str | Path, *, max_files: int = MAX_FILES) -> list[dict]:
    root = require_absolute(folder, name="folder").resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"folder not found: {root}")
    records: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not _should_skip_dir(name)]
        current = Path(dirpath)
        for name in filenames:
            if name.startswith("."):
                continue
            path = (current / name).resolve()
            try:
                stat = path.stat()
            except OSError:
                continue
            if not path.is_file() or stat.st_size > MAX_FILE_BYTES:
                continue
            try:
                digest = hash_file(path)
            except OSError:
                continue
            records.append(
                {
                    "path": str(path),
                    "folder": str(root),
                    "name": name,
                    "sha256": digest,
                    "size": int(stat.st_size),
                    "mtime": float(stat.st_mtime),
                }
            )
            if len(records) >= max_files:
                return records
    return records
