"""Read-only health report and privacy-preserving support bundle."""

from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path
from typing import Any

from .backup import BackupManager
from .operations import OperationLogger, redact_value
from .paths import MemoryConfig, atomic_write_text, require_absolute
from .security import private_path_status
from .store import MemoryStore


def _permissions(path: Path, expected: int) -> dict[str, Any]:
    return private_path_status(path, expected=expected)


def _disk_encryption() -> dict[str, str]:
    system = platform.system().lower()
    if system == "darwin":
        try:
            result = subprocess.run(
                ["/usr/bin/fdesetup", "status"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            enabled = "FileVault is On" in result.stdout
            return {"provider": "FileVault", "status": "enabled" if enabled else "not_enabled"}
        except (OSError, subprocess.SubprocessError):
            return {"provider": "FileVault", "status": "not_verified"}
    if system == "windows":
        return {"provider": "BitLocker", "status": "not_verified"}
    return {"provider": "LUKS/full-disk encryption", "status": "not_verified"}


def _update_verifier() -> dict[str, Any]:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        available = Ed25519PublicKey is not None
    except (ImportError, OSError):
        available = False
    return {"ok": available, "algorithm": "Ed25519"}


def doctor_report(
    data_dir: str | Path,
    store: MemoryStore,
    config: MemoryConfig | None,
) -> dict[str, Any]:
    data = require_absolute(data_dir, name="data dir")
    manager = BackupManager(data)
    backups = manager.list()
    latest_backup = None
    if backups:
        try:
            latest_backup = manager.verify(backups[0]["path"])
        except Exception as exc:  # noqa: BLE001 - doctor reports, never mutates
            latest_backup = {"ok": False, "error": type(exc).__name__}
    scope_issues = 0
    scope_issue_rows: list[dict[str, Any]] = []
    scan_health: list[dict[str, Any]] = []
    permission_health: dict[str, Any] = {}
    if store.schema_version >= 2:
        scope_issue_rows = [
            {
                "issue_code": str(row["issue_code"]),
                "object_type": str(row["object_type"]),
                "object_id": str(row["object_id"]),
            }
            for row in store.connection.execute(
                "SELECT issue_code, object_type, object_id FROM migration_issues ORDER BY issue_id"
            )
        ]
        scope_issues = sum(row["issue_code"] == "AMBIGUOUS_SCOPE_TAG" for row in scope_issue_rows)
        scan_health = [
            dict(row)
            for row in store.connection.execute(
                """
                SELECT s.source_uid, s.status AS source_status, sr.run_uid,
                       sr.status AS run_status, sr.completed_at, sr.truncated, sr.error_code
                FROM sources s LEFT JOIN scan_runs sr ON sr.run_uid = (
                    SELECT run_uid FROM scan_runs WHERE source_id = s.source_id
                    ORDER BY started_at DESC, rowid DESC LIMIT 1
                ) ORDER BY s.source_id
                """
            ).fetchall()
        ]
        permission_health = {
            "principals": int(store.connection.execute("SELECT COUNT(*) FROM principals").fetchone()[0]),
            "enabled_principals": int(
                store.connection.execute("SELECT COUNT(*) FROM principals WHERE enabled = 1").fetchone()[0]
            ),
            "grants": int(store.connection.execute("SELECT COUNT(*) FROM principal_grants").fetchone()[0]),
        }
    integrity = store.integrity()
    projection = store.projection_state()
    counts = store.counts()
    paths = {
        "data_dir": _permissions(data, 0o700),
        "database": _permissions(Path(store.db_path), 0o600),
    }
    warnings: list[str] = []
    if not integrity["ok"]:
        warnings.append("database_integrity_failed")
    if scope_issues:
        warnings.append("ambiguous_scope_tags")
    if projection.get("status") == "dirty":
        warnings.append("projection_dirty")
    if any(not value.get("ok", True) for value in paths.values()):
        warnings.append("filesystem_permissions")
    encryption = _disk_encryption()
    if encryption["status"] != "enabled":
        warnings.append("disk_encryption_not_verified")
    update_verifier = _update_verifier()
    if not update_verifier["ok"]:
        warnings.append("update_verifier_unavailable")
    return {
        "ok": not any(
            item in warnings
            for item in ("database_integrity_failed", "filesystem_permissions", "update_verifier_unavailable")
        ),
        "schema": {
            "current": store.schema_version,
            "library_uid": store.library_uid if store.schema_version >= 2 else None,
        },
        "database": integrity,
        "permissions": paths,
        "disk_encryption": encryption,
        "update_verification": update_verifier,
        "backup": {"count": len(backups), "latest": latest_backup},
        "projection": projection,
        "index": {
            "files": counts["files"],
            "files_current": counts.get("files_current", counts["files"]),
            "scans": scan_health,
        },
        "access_control": permission_health,
        "migration_scope_issues": scope_issues,
        "migration_issues": scope_issue_rows,
        "config": {
            "present": config is not None,
            "obsidian_enabled": bool(config.obsidian_enabled) if config else False,
            "locale": config.locale if config else None,
        },
        "warnings": warnings,
    }


def create_support_bundle(
    data_dir: str | Path,
    store: MemoryStore,
    config: MemoryConfig | None,
    output: str | Path,
) -> dict[str, Any]:
    """Default bundle intentionally excludes fact content and complete paths."""
    target = require_absolute(output, name="support bundle")
    report = doctor_report(data_dir, store, config)
    sanitized_config = None
    if config:
        sanitized_config = {
            "schema_version": config.schema_version,
            "tier": config.tier,
            "locale": config.locale,
            "obsidian_enabled": config.obsidian_enabled,
            "data_dir": "<redacted>",
            "db_path": "<redacted>",
            "vault_dir": "<redacted>",
        }
    payload = {
        "format": "with-support-bundle-v1",
        "version": __import__("eric_memory").__version__,
        "health": redact_value(report),
        "config": sanitized_config,
        "logs": OperationLogger(data_dir).tail(200),
        "privacy": {"facts_included": False, "paths_included": False},
    }
    atomic_write_text(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n", mode=0o600)
    return {"path": str(target), "facts_included": False, "paths_included": False}
