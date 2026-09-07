"""Redacted local operation logging."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .paths import require_absolute
from .security import harden_private_path
from .store import utc_now

_REDACTED_KEYS = {
    "content",
    "body",
    "text",
    "path",
    "source_locator",
    "root",
    "canonical_root",
    "data_dir",
    "db_path",
    "vault_dir",
}


def redact_value(value: Any, *, key: str = "") -> Any:
    if key.casefold() in _REDACTED_KEYS:
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): redact_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value[:100]]
    if isinstance(value, str):
        if os.path.isabs(value):
            return "<redacted-path>"
        return value[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


class OperationLogger:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = require_absolute(data_dir, name="data dir")
        self.log_dir = self.data_dir / "logs"
        self.log_path = self.log_dir / "with.jsonl"

    def write(
        self,
        *,
        operation_uid: str,
        action: str,
        outcome: str,
        principal: str,
        error_code: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        harden_private_path(self.log_dir, directory=True)
        payload = {
            "timestamp": utc_now(),
            "operation_uid": operation_uid,
            "action": action,
            "outcome": outcome,
            "principal": principal,
            "error_code": error_code,
            "metadata": redact_value(metadata or {}),
        }
        raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        harden_private_path(self.log_path, directory=False)

    def tail(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.log_path.is_file():
            return []
        lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        result: list[dict[str, Any]] = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                result.append(redact_value(item))
        return result
