"""Incrementally index user-approved roots without following symbolic links."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import TruncatedScanError, ValidationError
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
MAX_FILES = 100_000


@dataclass(frozen=True)
class ScanResult:
    root: str
    changed: list[dict[str, Any]]
    unchanged_paths: list[str]
    seen_paths: set[str]
    skipped: int
    truncated: bool

    @property
    def total(self) -> int:
        return len(self.seen_paths)


def _should_skip_dir(name: str) -> bool:
    return name in SKIP_DIR_NAMES or name.startswith(".")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_regular_file_no_follow(
    path: Path,
    *,
    root: Path,
    expected: os.stat_result,
) -> tuple[str, os.stat_result]:
    """Hash the same regular file that was lstat'ed, never a final symlink."""
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if not stat_module.S_ISREG(opened.st_mode):
            raise OSError("source entry is not a regular file")
        # st_ino/st_dev bind the opened descriptor to the entry inspected by
        # lstat. They are meaningful on POSIX and harmless where reported as 0.
        if (
            getattr(expected, "st_ino", 0)
            and getattr(opened, "st_ino", 0)
            and (expected.st_dev, expected.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise OSError("source entry changed during scan")
        canonical_after = path.resolve(strict=True)
        if not canonical_after.is_relative_to(root):
            raise OSError("source entry escaped its approved root")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        final = os.fstat(fd)
        if (opened.st_size, opened.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
            raise OSError("source entry changed while it was being hashed")
        final_lstat = path.lstat()
        if stat_module.S_ISLNK(final_lstat.st_mode):
            raise OSError("source entry became a symbolic link")
        if (
            getattr(final_lstat, "st_ino", 0)
            and getattr(opened, "st_ino", 0)
            and (final_lstat.st_dev, final_lstat.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise OSError("source entry was replaced during scan")
        return digest.hexdigest(), final
    finally:
        os.close(fd)


def _matches(
    relative: str,
    *,
    include: list[str],
    exclude: list[str],
    file_types: set[str],
) -> bool:
    name = Path(relative).name
    if include and not any(fnmatch.fnmatchcase(relative, item) or fnmatch.fnmatchcase(name, item) for item in include):
        return False
    if any(fnmatch.fnmatchcase(relative, item) or fnmatch.fnmatchcase(name, item) for item in exclude):
        return False
    if file_types:
        suffix = Path(relative).suffix.casefold().lstrip(".")
        if suffix not in file_types:
            return False
    return True


def scan_folder(
    folder: str | Path,
    *,
    previous: dict[str, dict[str, Any]] | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    file_types: list[str] | None = None,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_files: int = MAX_FILES,
) -> ScanResult:
    if max_files < 1:
        raise ValidationError("max_files must be positive")
    if max_file_bytes < 1:
        raise ValidationError("max_file_bytes must be positive")
    requested = require_absolute(folder, name="folder")
    root = requested.resolve(strict=True)
    if not root.is_dir():
        raise FileNotFoundError(f"folder not found: {root}")
    prior = previous or {}
    include_patterns = list(include or [])
    exclude_patterns = list(exclude or [])
    extensions = {item.casefold().lstrip(".") for item in (file_types or []) if item.strip()}
    changed: list[dict[str, Any]] = []
    unchanged: list[str] = []
    seen: set[str] = set()
    skipped = 0
    truncated = False

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(dirpath)
        safe_dirs: list[str] = []
        for dirname in dirnames:
            child = current / dirname
            try:
                child_stat = child.lstat()
            except OSError:
                skipped += 1
                continue
            if _should_skip_dir(dirname) or stat_module.S_ISLNK(child_stat.st_mode):
                skipped += 1
                continue
            try:
                canonical = child.resolve(strict=True)
            except OSError:
                skipped += 1
                continue
            if not canonical.is_relative_to(root):
                skipped += 1
                continue
            safe_dirs.append(dirname)
        dirnames[:] = safe_dirs

        for name in filenames:
            if name.startswith("."):
                skipped += 1
                continue
            path = current / name
            try:
                file_stat = path.lstat()
            except OSError:
                skipped += 1
                continue
            if stat_module.S_ISLNK(file_stat.st_mode) or not stat_module.S_ISREG(file_stat.st_mode):
                skipped += 1
                continue
            try:
                canonical_path = path.resolve(strict=True)
            except OSError:
                skipped += 1
                continue
            if not canonical_path.is_relative_to(root):
                skipped += 1
                continue
            relative = canonical_path.relative_to(root).as_posix()
            if not _matches(
                relative,
                include=include_patterns,
                exclude=exclude_patterns,
                file_types=extensions,
            ):
                skipped += 1
                continue
            if file_stat.st_size > max_file_bytes:
                skipped += 1
                continue
            if len(seen) >= max_files:
                truncated = True
                break

            path_string = str(canonical_path)
            seen.add(path_string)
            old = prior.get(path_string)
            old_mtime_ns = None
            if old is not None:
                if "mtime_ns" in old:
                    old_mtime_ns = int(old["mtime_ns"])
                elif "mtime" in old:
                    old_mtime_ns = int(float(old["mtime"]) * 1_000_000_000)
            if (
                old is not None
                and int(old.get("size", -1)) == int(file_stat.st_size)
                and old_mtime_ns == int(file_stat.st_mtime_ns)
            ):
                unchanged.append(path_string)
                continue
            try:
                digest, hashed_stat = _hash_regular_file_no_follow(
                    path,
                    root=root,
                    expected=file_stat,
                )
            except OSError:
                skipped += 1
                seen.discard(path_string)
                continue
            changed.append(
                {
                    "path": path_string,
                    "folder": str(root),
                    "name": name,
                    "sha256": digest,
                    "size": int(hashed_stat.st_size),
                    "mtime": float(hashed_stat.st_mtime),
                    "mtime_ns": int(hashed_stat.st_mtime_ns),
                }
            )
        if truncated:
            break

    return ScanResult(
        root=str(root),
        changed=changed,
        unchanged_paths=unchanged,
        seen_paths=seen,
        skipped=skipped,
        truncated=truncated,
    )


def walk_folder(folder: str | Path, *, max_files: int = MAX_FILES) -> list[dict[str, Any]]:
    """Compatibility wrapper for callers that need a complete fresh snapshot."""
    result = scan_folder(folder, max_files=max_files)
    if result.truncated:
        raise TruncatedScanError(
            f"folder contains more than {max_files} eligible files",
            details={"max_files": max_files},
        )
    return result.changed
