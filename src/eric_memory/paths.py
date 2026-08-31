"""Absolute-path policy: expand HOME once, never pass '~' into runtime IO."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_NAME = "config.json"
POINTER_NAME = ".data-dir"
DEFAULT_DATA_DIRNAME = "eric-memory-data"
SCHEMA_VERSION = 1


class PathError(ValueError):
    """Raised when a path is missing, relative, or still contains '~'."""


def _has_unexpanded_home(raw: str) -> bool:
    folded = raw.casefold()
    return (
        "~" in raw
        or "$home" in folded
        or "${home}" in folded
        or "%userprofile%" in folded
    )


def require_absolute(path: str | Path, *, name: str) -> Path:
    raw = str(path)
    if _has_unexpanded_home(raw):
        raise PathError(f"{name} must be an already-expanded absolute path, got {raw!r}")
    p = Path(raw)
    if not p.is_absolute():
        raise PathError(f"{name} must be an absolute path, got {raw!r}")
    return p


def expand_once(path: str | Path, *, name: str) -> Path:
    """Allow '~' / $HOME / %USERPROFILE% only at the boundary, then freeze the result."""
    raw = str(path).strip()
    if not raw:
        raise PathError(f"{name} is empty")
    expanded = os.path.expandvars(os.path.expanduser(raw))
    candidate = Path(expanded)
    # resolve() would turn leftover $HOME or %USERPROFILE% into cwd/<literal>.
    if not candidate.is_absolute():
        raise PathError(f"{name} must be an already-expanded absolute path, got {raw!r}")
    return require_absolute(candidate.resolve(), name=name)


def default_data_dir() -> Path:
    return require_absolute(Path.home() / DEFAULT_DATA_DIRNAME, name="default data dir")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


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
    pointer_path(root).write_text(str(abs_dir) + "\n", encoding="utf-8")


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
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "data_dir": self.data_dir,
            "vault_dir": self.vault_dir,
            "db_path": self.db_path,
            "tier": self.tier,
            "locale": self.locale,
            "schema_version": self.schema_version,
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
        }
        return cls(
            data_dir=str(data["data_dir"]),
            vault_dir=str(data["vault_dir"]),
            db_path=str(data["db_path"]),
            tier=str(data.get("tier", "simple")),
            locale=str(data.get("locale", "zh")),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
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
    data_dir.mkdir(parents=True, exist_ok=True)
    path = config_path(data_dir)
    path.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
