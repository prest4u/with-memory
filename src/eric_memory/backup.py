"""Verified SQLite backups, rotation, and atomic restore."""

from __future__ import annotations

import builtins
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import IntegrityCheckError, ValidationError
from .paths import atomic_write_text, require_absolute
from .security import harden_private_path
from .store import MemoryStore, search_terms, utc_now

MANAGED_SUFFIX = ".sqlite3"


def _sql_tokens(sql: str) -> list[str]:
    # Ignore formatting and keyword case while preserving quoted values.
    tokens = re.findall(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`(?:[^`]|``)*`|\[[^\]]*\]|\w+|[^\s]", sql)
    return [token if token[0] in "'\"`[" else token.casefold() for token in tokens]


def validate_restore_schema(path: Path) -> None:
    """Reject unsupported or incomplete schemas before replacing a live library."""
    from .migration import LEGACY_SCHEMA_V1
    from .schema import SCHEMA_V2

    health = inspect_database(path)
    version = health["schema_version"]
    if version not in {1, 2}:
        raise ValidationError("backup schema is not supported for restore")
    actual = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    expected = sqlite3.connect(":memory:")
    try:
        expected.executescript(LEGACY_SCHEMA_V1 if version == 1 else SCHEMA_V2)
        for (table,) in expected.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            quoted = '"' + table.replace('"', '""') + '"'
            required = {row[1] for row in expected.execute(f"PRAGMA table_info({quoted})")}
            available = {row[1] for row in actual.execute(f"PRAGMA table_info({quoted})")}
            if not required <= available:
                raise ValidationError("backup is missing required schema structure")
        for kind, name, sql in expected.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL"):
            if kind not in {"trigger", "index", "view"} and not sql.upper().startswith("CREATE VIRTUAL TABLE"):
                continue
            row = actual.execute("SELECT sql FROM sqlite_master WHERE type = ? AND name = ?", (kind, name)).fetchone()
            if row is None or _sql_tokens(str(row[0])) != _sql_tokens(sql):
                raise ValidationError("backup has missing or incompatible triggers, indexes or virtual tables")
        # Check postings against external content on a private writable copy.
        # The source backup remains read-only and no live context has been reset.
        probe = sqlite3.connect(":memory:")
        try:
            actual.backup(probe)
            probe.execute("INSERT INTO facts_fts(facts_fts, rank) VALUES('integrity-check', 1)")
            if version >= 2:
                for fact_id, content, tags in actual.execute("SELECT fact_id, content, tags FROM facts"):
                    indexed = set(
                        actual.execute("SELECT term, kind FROM fact_search_terms WHERE fact_id = ?", (fact_id,))
                    )
                    if indexed != search_terms(f"{content} {tags}"):
                        raise ValidationError("backup has inconsistent search terms")
        except sqlite3.DatabaseError as exc:
            raise ValidationError("backup has inconsistent full-text postings") from exc
        finally:
            probe.close()
    finally:
        expected.close()
        actual.close()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def inspect_database(path: Path) -> dict[str, Any]:
    target = require_absolute(path, name="database")
    if not target.is_file():
        raise FileNotFoundError(f"database not found: {target}")
    conn = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        integrity = [str(row[0]) for row in conn.execute("PRAGMA integrity_check").fetchall()]
        foreign_keys = [dict(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]
        schema_row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        schema_version = int(schema_row["value"]) if schema_row else 0
        facts = int(conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
        return {
            "ok": integrity == ["ok"] and not foreign_keys,
            "integrity": integrity,
            "foreign_key_violations": foreign_keys,
            "schema_version": schema_version,
            "facts": facts,
        }
    finally:
        conn.close()


class BackupManager:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = require_absolute(data_dir, name="data dir")
        self.backup_dir = self.data_dir / "backups"

    def _prepare_dir(self) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        harden_private_path(self.backup_dir, directory=True)

    @staticmethod
    def _manifest_path(backup: Path) -> Path:
        return backup.with_suffix(backup.suffix + ".json")

    def create(self, store: MemoryStore, *, kind: str = "manual") -> dict[str, Any]:
        with store.maintenance_lock():
            return self.create_from_connection(
                store.connection,
                schema_version=store.schema_version,
                kind=kind,
            )

    def create_from_connection(
        self,
        connection: sqlite3.Connection,
        *,
        schema_version: int,
        kind: str,
    ) -> dict[str, Any]:
        if not kind or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in kind):
            raise ValidationError("backup kind contains invalid characters")
        self._prepare_dir()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = self.backup_dir / f"{kind}-{stamp}-{uuid.uuid4().hex[:8]}{MANAGED_SUFFIX}"
        fd, temp_name = tempfile.mkstemp(prefix=".backup-", suffix=".tmp", dir=self.backup_dir)
        os.close(fd)
        temp = Path(temp_name)
        try:
            destination = sqlite3.connect(str(temp))
            try:
                connection.backup(destination)
                destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                destination.commit()
            finally:
                destination.close()
            if os.name != "nt":
                os.chmod(temp, 0o600)
            health = inspect_database(temp)
            if not health["ok"]:
                raise IntegrityCheckError("new backup failed SQLite integrity checks")
            digest = sha256_file(temp)
            os.replace(temp, backup)
            harden_private_path(backup, directory=False)
            _fsync_directory(self.backup_dir)
            manifest = {
                "format": "with-backup-v1",
                "kind": kind,
                "created_at": utc_now(),
                "schema_version": schema_version,
                "filename": backup.name,
                "sha256": digest,
                "size": backup.stat().st_size,
                "facts": health["facts"],
            }
            atomic_write_text(
                self._manifest_path(backup).resolve(),
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                mode=0o600,
            )
            return {**manifest, "path": str(backup), "verified": True}
        except Exception:
            temp.unlink(missing_ok=True)
            backup.unlink(missing_ok=True)
            self._manifest_path(backup).unlink(missing_ok=True)
            raise

    def list(self) -> list[dict[str, Any]]:
        if not self.backup_dir.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for manifest_path in sorted(self.backup_dir.glob(f"*{MANAGED_SUFFIX}.json"), reverse=True):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                backup = self.backup_dir / str(data["filename"])
                if not backup.is_file() or backup.parent != self.backup_dir:
                    continue
                items.append({**data, "path": str(backup)})
            except (OSError, ValueError, KeyError, TypeError):
                continue

        def creation_order(item: dict[str, Any]) -> tuple[str, str]:
            stamp = re.search(r"-(\d{8}T\d{12}Z)-", str(item.get("filename", "")))
            return str(item.get("created_at", "")), stamp.group(1) if stamp else ""

        items.sort(key=creation_order, reverse=True)
        return items

    def verify(self, backup_path: str | Path) -> dict[str, Any]:
        backup = require_absolute(backup_path, name="backup")
        if not backup.is_file():
            raise FileNotFoundError(f"backup not found: {backup}")
        health = inspect_database(backup)
        digest = sha256_file(backup)
        manifest_path = self._manifest_path(backup)
        expected = None
        if manifest_path.is_file():
            expected = json.loads(manifest_path.read_text(encoding="utf-8")).get("sha256")
        hash_ok = expected is None or expected == digest
        return {
            "path": str(backup),
            "sha256": digest,
            "expected_sha256": expected,
            "hash_ok": hash_ok,
            "database": health,
            "ok": bool(hash_ok and health["ok"]),
        }

    def maybe_daily(self, store: MemoryStore) -> dict[str, Any] | None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if any(
            item.get("kind") == "automatic" and str(item.get("created_at", "")).startswith(today)
            for item in self.list()
        ):
            return None
        created = self.create(store, kind="automatic")
        self.prune()
        return created

    def prune(self) -> dict[str, Any]:
        automatic = [item for item in self.list() if item.get("kind") == "automatic"]
        automatic.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        keep: set[str] = set()
        daily: set[str] = set()
        weekly: set[str] = set()
        monthly: set[str] = set()
        for item in automatic:
            created = datetime.fromisoformat(str(item["created_at"]).replace("Z", "+00:00"))
            day_key = created.strftime("%Y-%m-%d")
            week_key = f"{created.isocalendar().year}-W{created.isocalendar().week:02d}"
            month_key = created.strftime("%Y-%m")
            should_keep = False
            if len(daily) < 7 and day_key not in daily:
                daily.add(day_key)
                should_keep = True
            elif len(weekly) < 4 and week_key not in weekly:
                weekly.add(week_key)
                should_keep = True
            elif len(monthly) < 12 and month_key not in monthly:
                monthly.add(month_key)
                should_keep = True
            if should_keep:
                keep.add(str(item["path"]))
        removed: list[str] = []
        for item in automatic:
            path = Path(str(item["path"]))
            if str(path) in keep:
                continue
            path.unlink(missing_ok=True)
            self._manifest_path(path).unlink(missing_ok=True)
            removed.append(str(path))
        return {"kept": len(keep), "removed": removed}

    def backups_containing_fact(
        self,
        *,
        fact_uid: str,
        content: str,
        normalized_hash: str,
    ) -> builtins.list[str]:
        matching: builtins.list[str] = []
        for item in self.list():
            path = Path(str(item["path"]))
            conn: sqlite3.Connection | None = None
            try:
                conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(facts)").fetchall()}
                if "fact_uid" in columns:
                    row = conn.execute(
                        "SELECT 1 FROM facts WHERE fact_uid = ? OR content = ? OR normalized_content_hash = ? LIMIT 1",
                        (fact_uid, content, normalized_hash),
                    ).fetchone()
                else:
                    row = conn.execute("SELECT 1 FROM facts WHERE content = ? LIMIT 1", (content,)).fetchone()
                if row:
                    matching.append(str(path))
            except sqlite3.DatabaseError:
                continue
            finally:
                if conn is not None:
                    conn.close()
        return matching

    def remove_managed(self, paths: builtins.list[str]) -> builtins.list[str]:
        removed: builtins.list[str] = []
        managed_root = self.backup_dir.resolve()
        targets = []
        for raw in paths:
            path = require_absolute(raw, name="backup").resolve()
            if path.parent != managed_root or path.suffix != MANAGED_SUFFIX:
                raise ValidationError("refusing to remove a backup outside the managed backup directory")
            targets.append(path)
        for path in targets:
            path.unlink(missing_ok=True)
            self._manifest_path(path).unlink(missing_ok=True)
            removed.append(str(path))
        return removed


def atomic_restore_database(
    db_path: str | Path,
    backup_path: str | Path,
    *,
    expected_sha256: str | None = None,
    before_replace: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    target = require_absolute(db_path, name="database")
    source = require_absolute(backup_path, name="backup")
    health = inspect_database(source)
    if not health["ok"]:
        raise IntegrityCheckError("backup failed integrity checks")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.restore-", suffix=".tmp", dir=target.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        if expected_sha256 is not None:
            source_wal = Path(str(source) + "-wal")
            if source_wal.exists() and source_wal.stat().st_size:
                raise ValidationError("restore requires a closed standalone backup without pending WAL writes")
            shutil.copyfile(source, temp)
            if source_wal.exists() and source_wal.stat().st_size:
                raise ValidationError("backup changed after verification")
            if sha256_file(temp) != expected_sha256:
                raise ValidationError("backup changed after verification")
        else:
            source_conn = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
            destination = sqlite3.connect(str(temp))
            try:
                source_conn.backup(destination)
                destination.commit()
            finally:
                destination.close()
                source_conn.close()
        copied_health = inspect_database(temp)
        if not copied_health["ok"]:
            raise IntegrityCheckError("restored temporary database failed integrity checks")
        validate_restore_schema(temp)
        if os.name != "nt":
            os.chmod(temp, 0o600)
        if before_replace is not None:
            before_replace()
        os.replace(temp, target)
        _fsync_directory(target.parent)
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        return {"db_path": str(target), "from": str(source), "database": copied_health}
    except Exception:
        temp.unlink(missing_ok=True)
        raise
