"""Absolute-path policy: expand HOME once, never pass '~' into runtime IO."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .security import harden_private_path

CONFIG_NAME = "config.json"
POINTER_NAME = ".data-dir"
DEFAULT_DATA_DIRNAME = "eric-memory-data"
SCHEMA_VERSION = 2


class PathError(ValueError):
    """Raised when a path is missing, relative, or still contains '~'."""


def _has_unexpanded_home(raw: str) -> bool:
    folded = raw.casefold()
    return "~" in raw or "$home" in folded or "${home}" in folded or "%userprofile%" in folded


def require_absolute(path: str | Path, *, name: str) -> Path:
    raw = str(path)
    if _has_unexpanded_home(raw):
        raise PathError(f"{name} must be an already-expanded absolute path, got {raw!r}")
    p = Path(raw)
    if not p.is_absolute():
        raise PathError(f"{name} must be an absolute path, got {raw!r}")
    return p


def expand_once(path: str | Path, *, name: str, resolve: bool = True) -> Path:
    """Allow '~' / $HOME / %USERPROFILE% only at the boundary, then freeze the result."""
    raw = str(path).strip()
    if not raw:
        raise PathError(f"{name} is empty")
    expanded = os.path.expandvars(os.path.expanduser(raw))
    candidate = Path(expanded)
    # resolve() would turn leftover $HOME or %USERPROFILE% into cwd/<literal>.
    if not candidate.is_absolute():
        raise PathError(f"{name} must be an already-expanded absolute path, got {raw!r}")
    # Source-consent code must inspect the user-supplied path for symlink
    # components before canonicalizing it. Other boundaries retain the legacy
    # canonical behavior by default.
    frozen = candidate.resolve() if resolve else Path(os.path.abspath(candidate))
    return require_absolute(frozen, name=name)


def default_data_dir() -> Path:
    return require_absolute(Path.home() / DEFAULT_DATA_DIRNAME, name="default data dir")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resource_root() -> Path:
    """Runtime resources live inside the package in wheels and frozen builds."""
    packaged = Path(__file__).resolve().parent / "resources"
    if packaged.is_dir():
        return packaged
    return repo_root()


def pointer_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / POINTER_NAME


def read_pointer(root: Path | None = None) -> Path | None:
    p = pointer_path(root)
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return None
    return require_absolute(text, name="data-dir pointer")


def write_pointer(data_dir: Path, root: Path | None = None) -> None:
    abs_dir = require_absolute(data_dir, name="data dir")
    atomic_write_text(pointer_path(root).resolve(), str(abs_dir) + "\n", mode=0o600)


def resolve_data_dir(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return expand_once(explicit, name="data dir")
    env = os.environ.get("ERIC_MEMORY_DATA", "").strip()
    if env:
        return expand_once(env, name="ERIC_MEMORY_DATA")
    pointed = read_pointer()
    if pointed is not None:
        return pointed
    return default_data_dir()


@dataclass
class MemoryConfig:
    data_dir: str
    vault_dir: str
    db_path: str
    tier: str = "simple"
    locale: str = "zh"
    schema_version: int = SCHEMA_VERSION
    obsidian_enabled: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "data_dir": self.data_dir,
            "vault_dir": self.vault_dir,
            "db_path": self.db_path,
            "tier": self.tier,
            "locale": self.locale,
            "schema_version": self.schema_version,
            "obsidian_enabled": self.obsidian_enabled,
        }
        payload.update(self.extra)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryConfig:
        known = {
            "data_dir",
            "vault_dir",
            "db_path",
            "tier",
            "locale",
            "schema_version",
            "obsidian_enabled",
        }
        return cls(
            data_dir=str(data["data_dir"]),
            vault_dir=str(data["vault_dir"]),
            db_path=str(data["db_path"]),
            tier=str(data.get("tier", "simple")),
            locale=str(data.get("locale", "zh")),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            obsidian_enabled=bool(data.get("obsidian_enabled", bool(data.get("vault_dir")))),
            extra={k: v for k, v in data.items() if k not in known},
        )


def config_path(data_dir: Path) -> Path:
    return require_absolute(data_dir, name="data dir") / CONFIG_NAME


def db_path_for(data_dir: Path) -> Path:
    return require_absolute(data_dir, name="data dir") / "memory.db"


def default_vault_dir(data_dir: Path) -> Path:
    return require_absolute(data_dir, name="data dir") / "vault"


def load_config(data_dir: Path) -> MemoryConfig | None:
    path = config_path(data_dir)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    cfg = MemoryConfig.from_dict(data)
    require_absolute(cfg.data_dir, name="config.data_dir")
    require_absolute(cfg.vault_dir, name="config.vault_dir")
    require_absolute(cfg.db_path, name="config.db_path")
    return cfg


def save_config(cfg: MemoryConfig) -> Path:
    data_dir = require_absolute(cfg.data_dir, name="config.data_dir")
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    harden_private_path(data_dir, directory=True)
    path = config_path(data_dir)
    atomic_write_text(path, json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2) + "\n", mode=0o600)
    harden_private_path(path, directory=False)
    return path


def atomic_write_text(path: str | Path, content: str, *, mode: int = 0o600) -> Path:
    """fsync a sibling temporary file, replace atomically, then fsync the directory."""
    target = require_absolute(path, name="output path")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temp_path = Path(temporary)
    try:
        if os.name != "nt":
            os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        harden_private_path(target, directory=False)
        if os.name != "nt":
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        with suppress(OSError):
            temp_path.unlink(missing_ok=True)
        raise
    return target
