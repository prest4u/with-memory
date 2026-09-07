#!/usr/bin/env python3
"""Create one deterministic-ish ZIP from a native PyInstaller onedir build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path

PLATFORMS = {
    "macos-arm64": ("darwin", {"arm64", "aarch64"}),
    "macos-x86_64": ("darwin", {"x86_64", "amd64"}),
    "windows-x86_64": ("windows", {"x86_64", "amd64"}),
    "linux-x86_64": ("linux", {"x86_64", "amd64"}),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_native(tag: str) -> None:
    expected_system, expected_machines = PLATFORMS[tag]
    actual = (platform.system().lower(), platform.machine().lower())
    if actual[0] != expected_system or actual[1] not in expected_machines:
        raise SystemExit(f"refusing cross-platform package: requested {tag}, running on {actual}")


def _copy_public_payload(root: Path, collection: Path) -> None:
    root = root.resolve(strict=True)
    for name in (
        "skills",
        "quests",
        "adapters",
        "docs",
        "vault-template",
        "LICENSE",
        "README.md",
        "README.zh-CN.md",
        "SECURITY.md",
        "SECURITY.zh-CN.md",
        "pyproject.toml",
        "mcp/server.py",
        "bin/eric-memory",
    ):
        if not (root / name).resolve(strict=True).is_relative_to(root):
            raise ValueError("public payload links outside the repository")
    for name in ("skills", "quests", "adapters", "docs", "vault-template"):
        destination = collection / name
        if destination.exists():
            shutil.rmtree(destination)
        _copy_contained_tree(root / name, destination)
    for name in ("LICENSE", "README.md", "README.zh-CN.md", "SECURITY.md", "SECURITY.zh-CN.md", "pyproject.toml"):
        shutil.copy2(root / name, collection / name)
    (collection / "mcp").mkdir(exist_ok=True)
    shutil.copy2(root / "mcp" / "server.py", collection / "mcp" / "server.py")
    (collection / "bin").mkdir(exist_ok=True)
    shutil.copy2(root / "bin" / "eric-memory", collection / "bin" / "eric-memory")


def _copy_contained_tree(source: Path, destination: Path) -> None:
    """Materialize internal framework links, rejecting escapes and directory cycles."""
    root = source.resolve(strict=True)

    def copy(entry: Path, target: Path, ancestors: frozenset[Path]) -> None:
        resolved = entry.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError("package input links outside its source tree")
        if resolved.is_dir():
            if resolved in ancestors:
                raise ValueError("package input contains a directory link cycle")
            target.mkdir()
            for child in sorted(resolved.iterdir()):
                copy(child, target / child.name, ancestors | {resolved})
        elif resolved.is_file():
            shutil.copy2(resolved, target)
        else:
            raise ValueError("package input must contain only regular files and directories")

    copy(root, destination, frozenset())


def _zip_tree(source: Path, output: Path) -> None:
    epoch = max(315532800, int(os.environ.get("SOURCE_DATE_EPOCH", "315532800")))
    timestamp = __import__("datetime").datetime.fromtimestamp(epoch, tz=__import__("datetime").timezone.utc)
    date_time = (
        timestamp.year,
        timestamp.month,
        timestamp.day,
        timestamp.hour,
        timestamp.minute,
        timestamp.second,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    # A macOS PyInstaller onedir contains relative framework symlinks. The
    # updater intentionally rejects links in archives, so materialize them as
    # ordinary files/directories before producing the portable ZIP.
    with tempfile.TemporaryDirectory(prefix="with-package-", dir=output.parent) as temporary:
        materialized = Path(temporary) / "with"
        _copy_contained_tree(source, materialized)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(materialized.rglob("*"), key=lambda item: item.relative_to(materialized).as_posix()):
                if not path.is_file() or path.is_symlink():
                    continue
                relative = Path("with") / path.relative_to(materialized)
                info = zipfile.ZipInfo(relative.as_posix(), date_time=date_time)
                mode = 0o755 if os.access(path, os.X_OK) else 0o644
                info.external_attr = (stat.S_IFREG | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    parser.add_argument("--collection", type=Path, default=Path("dist/with"))
    parser.add_argument("--output-dir", type=Path, default=Path("release"))
    args = parser.parse_args()
    _validate_native(args.platform)
    root = Path(__file__).resolve().parents[1]
    collection = args.collection.resolve()
    executable = collection / ("eric-memory.exe" if os.name == "nt" else "eric-memory")
    if not executable.is_file():
        raise SystemExit(f"missing PyInstaller executable: {executable}")
    completed = subprocess.run([str(executable), "--version"], capture_output=True, text=True, check=True, timeout=10)
    if f"eric-memory {args.version}" not in completed.stdout:
        raise SystemExit("frozen CLI version does not match requested release")
    _copy_public_payload(root, collection)
    output = args.output_dir.resolve() / f"with-{args.version}-{args.platform}.zip"
    _zip_tree(collection, output)
    metadata = {
        "path": str(output),
        "name": output.name,
        "sha256": _sha256(output),
        "size": output.stat().st_size,
        "platform": args.platform,
        "native": True,
    }
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
