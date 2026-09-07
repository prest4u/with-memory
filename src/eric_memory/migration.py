"""Side-by-side schema-v1 to schema-v2 migration and guarded rollback."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from .backup import BackupManager, atomic_restore_database, inspect_database, sha256_file
from .errors import ConfirmationRequiredError, ConflictError, IntegrityCheckError, ValidationError
from .locking import exclusive_file_lock
from .paths import atomic_write_text, load_config, require_absolute, save_config
from .permissions import DEFAULT_HARNESS_CAPABILITIES, FACT_SEARCH
from .schema import SCHEMA_VERSION
from .scopes import scope_from_tags
from .store import MemoryStore, normalized_content_hash, utc_now

LEGACY_SCHEMA_V1 = """
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1');
CREATE TABLE facts (
    fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    tags TEXT NOT NULL DEFAULT '',
    trust REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'active',
    as_of TEXT NOT NULL,
    superseded_by INTEGER REFERENCES facts(fact_id),
    source_kind TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_facts_active_content ON facts(content) WHERE status = 'active';
CREATE UNIQUE INDEX idx_facts_source ON facts(source_kind, source_ref) WHERE source_ref != '';
CREATE TABLE entities (
    entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL
);
CREATE TABLE fact_entities (
    fact_id INTEGER NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    PRIMARY KEY(fact_id, entity_id)
);
CREATE TABLE folders (
    folder_id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    folder TEXT NOT NULL,
    name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    indexed_at TEXT NOT NULL
);
CREATE TABLE harnesses (
    harness_id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    session_root TEXT NOT NULL DEFAULT '',
    mcp_mounted INTEGER NOT NULL DEFAULT 0,
    harvest_ok INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    fact_id INTEGER,
    actor TEXT NOT NULL DEFAULT 'cli',
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE facts_fts USING fts5(
    content, tags, content=facts, content_rowid=fact_id, tokenize='unicode61'
);
CREATE TRIGGER facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content, tags) VALUES (new.fact_id, new.content, new.tags);
END;
CREATE TRIGGER facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags)
    VALUES ('delete', old.fact_id, old.content, old.tags);
END;
CREATE TRIGGER facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags)
    VALUES ('delete', old.fact_id, old.content, old.tags);
    INSERT INTO facts_fts(rowid, content, tags) VALUES (new.fact_id, new.content, new.tags);
END;
"""


def schema_version(db_path: str | Path) -> int:
    path = require_absolute(db_path, name="database")
    if not path.is_file():
        raise FileNotFoundError(f"database not found: {path}")
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        try:
            row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        except sqlite3.DatabaseError as exc:
            raise IntegrityCheckError("database is missing valid schema metadata") from exc
        if row is None:
            raise IntegrityCheckError("database is missing schema metadata")
        return int(row[0])
    finally:
        conn.close()


def _read_rows(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    # Callers pass only names from the closed legacy-schema table set below.
    return conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608


def _supersession_cycle(rows: list[sqlite3.Row]) -> list[int]:
    successor = {int(row["fact_id"]): int(row["superseded_by"]) for row in rows if row["superseded_by"] is not None}
    for start in successor:
        seen: set[int] = set()
        current = start
        while current in successor:
            if current in seen:
                return sorted(seen | {current})
            seen.add(current)
            current = successor[current]
    return []


def migration_plan(db_path: str | Path) -> dict[str, Any]:
    path = require_absolute(db_path, name="database")
    version = schema_version(path)
    health = inspect_database(path)
    if version == SCHEMA_VERSION:
        return {
            "action": "none",
            "from_schema": version,
            "to_schema": SCHEMA_VERSION,
            "database": health,
            "blockers": [],
            "warnings": [],
        }
    if version != 1:
        raise ValidationError(f"unsupported source schema v{version}")
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return _plan_v1_snapshot(conn, health)
    finally:
        conn.close()


def _plan_v1_snapshot(conn: sqlite3.Connection, health: dict[str, Any]) -> dict[str, Any]:
    """Validate the same pinned snapshot that will be copied during apply."""
    facts = _read_rows(conn, "facts")
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    active_keys: dict[tuple[str, str], int] = {}
    for row in facts:
        scope, issues = scope_from_tags(str(row["tags"] or ""))
        if issues:
            warnings.append(
                {
                    "code": "AMBIGUOUS_SCOPE_TAG",
                    "fact_id": int(row["fact_id"]),
                    "issues": issues,
                }
            )
        if str(row["status"]) == "active":
            key = (normalized_content_hash(str(row["content"])), scope.fingerprint)
            if key in active_keys:
                blockers.append(
                    {
                        "code": "NORMALIZED_ACTIVE_COLLISION",
                        "fact_ids": [active_keys[key], int(row["fact_id"])],
                        "scope": scope.fingerprint,
                    }
                )
            active_keys[key] = int(row["fact_id"])
    cycle = _supersession_cycle(facts)
    if cycle:
        blockers.append({"code": "SUPERSESSION_CYCLE", "fact_ids": cycle})
    counts = {
        # `table` comes from the closed tuple immediately below.
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
        for table in (
            "facts",
            "entities",
            "fact_entities",
            "folders",
            "files",
            "harnesses",
            "audit",
        )
    }
    return {
        "action": "migrate",
        "from_schema": 1,
        "to_schema": SCHEMA_VERSION,
        "database": health,
        "counts": counts,
        "blockers": blockers,
        "warnings": warnings,
        "strategy": "verified backup -> side database -> validate -> atomic replace",
        "rollback_boundary": "before the first post-migration v2 write",
    }


def _replace_library_identity(target: MemoryStore, library_uid: str) -> None:
    device_uid = str(uuid.uuid5(uuid.UUID(library_uid), "migration-device"))
    with target.write_transaction():
        target.connection.execute("DELETE FROM devices")
        target.connection.execute("DELETE FROM libraries")
        target.connection.execute(
            "INSERT INTO libraries(library_uid, created_at) VALUES (?, ?)",
            (library_uid, utc_now()),
        )
        target.connection.execute(
            "INSERT INTO devices(device_uid, library_uid, label, created_at) VALUES (?, ?, 'migrated-device', ?)",
            (device_uid, library_uid, utc_now()),
        )


def _copy_entities(source: sqlite3.Connection, target: MemoryStore) -> None:
    with target.write_transaction():
        for row in source.execute("SELECT * FROM entities ORDER BY entity_id").fetchall():
            target.connection.execute(
                """
                INSERT INTO entities(entity_id, name, name_key, entity_type, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(row["entity_id"]),
                    str(row["name"]),
                    str(row["name_key"]),
                    str(row["entity_type"]),
                    str(row["created_at"]),
                ),
            )


def _copy_sources_and_files(
    source: sqlite3.Connection,
    target: MemoryStore,
    library_uid: str,
) -> None:
    folders = source.execute("SELECT * FROM folders ORDER BY folder_id").fetchall()
    harnesses = source.execute("SELECT * FROM harnesses ORDER BY harness_id").fetchall()
    roots: dict[str, str] = {str(row["path"]): "" for row in folders if bool(row["enabled"])}
    for harness in harnesses:
        if bool(harness["harvest_ok"]) and str(harness["session_root"]):
            root, key = str(harness["session_root"]), str(harness["key"])
            if roots.get(root) and roots[root] != key:
                raise ConflictError("legacy source has multiple harness owners; resolve ownership before migration")
            roots[root] = key
    root_uids: dict[str, str] = {
        root: str(uuid.uuid5(uuid.UUID(library_uid), f"legacy-source:{root}")) for root in roots
    }
    now = utc_now()
    with target.write_transaction():
        for row in folders:
            target.connection.execute(
                """
                INSERT INTO folders(folder_id, path, label, enabled, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(row["folder_id"]),
                    str(row["path"]),
                    str(row["label"]),
                    int(row["enabled"]),
                    str(row["created_at"]),
                ),
            )
        for root, harness_key in roots.items():
            target.connection.execute(
                """
                INSERT INTO sources(
                    source_uid, source_kind, canonical_root, max_file_bytes,
                    harness_key, status, approved_at, consent_uid
                ) VALUES (?, 'folder', ?, ?, ?, 'approved', ?, ?)
                """,
                (
                    root_uids[root],
                    root,
                    50 * 1024 * 1024,
                    harness_key,
                    now,
                    str(uuid.uuid5(uuid.UUID(library_uid), f"legacy-consent:{root}")),
                ),
            )
        source_ids = {
            str(row["canonical_root"]): int(row["source_id"])
            for row in target.connection.execute("SELECT source_id, canonical_root FROM sources").fetchall()
        }
        for row in source.execute("SELECT * FROM files ORDER BY file_id").fetchall():
            folder = str(row["folder"])
            source_uid = root_uids.get(folder, "")
            target.connection.execute(
                """
                INSERT INTO files(
                    file_id, path, folder, name, sha256, size, mtime,
                    indexed_at, status, source_uid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'current', ?)
                """,
                (
                    int(row["file_id"]),
                    str(row["path"]),
                    folder,
                    str(row["name"]),
                    str(row["sha256"]),
                    int(row["size"]),
                    float(row["mtime"]),
                    str(row["indexed_at"]),
                    source_uid,
                ),
            )
            if source_uid:
                target.connection.execute(
                    """
                    INSERT INTO source_files(
                        file_uid, source_id, path, name, sha256, size, mtime_ns,
                        status, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'current', ?)
                    """,
                    (
                        str(uuid.uuid5(uuid.UUID(source_uid), str(row["path"]))),
                        source_ids[folder],
                        str(row["path"]),
                        str(row["name"]),
                        str(row["sha256"]),
                        int(row["size"]),
                        int(float(row["mtime"]) * 1_000_000_000),
                        str(row["indexed_at"]),
                    ),
                )
        for row in harnesses:
            target.connection.execute(
                """
                INSERT INTO harnesses(
                    harness_id, key, display_name, session_root, mcp_mounted,
                    harvest_ok, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(row),
            )
            key = str(row["key"])
            now_value = str(row["created_at"] or now)
            cur = target.connection.execute(
                """
                INSERT INTO principals(
                    principal_uid, key, display_name, enabled, local_admin, created_at, updated_at
                ) VALUES (?, ?, ?, 1, 0, ?, ?)
                """,
                (
                    str(uuid.uuid5(uuid.UUID(library_uid), f"legacy-principal:{key}")),
                    key,
                    str(row["display_name"]),
                    now_value,
                    str(row["updated_at"] or now),
                ),
            )
            if cur.lastrowid is None:
                raise IntegrityCheckError("migration principal insert returned no row ID")
            principal_id = int(cur.lastrowid)
            user_scope_id = int(
                target.connection.execute("SELECT scope_id FROM scopes WHERE fingerprint = 'user:'").fetchone()[0]
            )
            for capability in DEFAULT_HARNESS_CAPABILITIES:
                target.connection.execute(
                    """
                    INSERT INTO principal_grants(principal_id, capability, scope_id, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        principal_id,
                        capability,
                        user_scope_id if capability == FACT_SEARCH else None,
                        now,
                    ),
                )


def _validate_copy(source: sqlite3.Connection, target: MemoryStore) -> dict[str, Any]:
    tables = ("facts", "entities", "fact_entities", "folders", "files", "harnesses", "audit")
    comparison: dict[str, dict[str, int]] = {}
    for table in tables:
        # `table` comes from the closed validation tuple above.
        before = int(source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
        after = int(target.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
        comparison[table] = {"before": before, "after": after}
        if before != after:
            raise IntegrityCheckError(f"migration row count mismatch for {table}: {before} != {after}")
    before_status = {
        str(row[0]): int(row[1])
        for row in source.execute("SELECT status, COUNT(*) FROM facts GROUP BY status").fetchall()
    }
    after_status = {
        str(row[0]): int(row[1])
        for row in target.connection.execute("SELECT status, COUNT(*) FROM facts GROUP BY status").fetchall()
    }
    if before_status != after_status:
        raise IntegrityCheckError("migration changed fact statuses")
    before_ids = [int(row[0]) for row in source.execute("SELECT fact_id FROM facts ORDER BY fact_id")]
    after_ids = [int(row[0]) for row in target.connection.execute("SELECT fact_id FROM facts ORDER BY fact_id")]
    if before_ids != after_ids:
        raise IntegrityCheckError("migration changed legacy fact IDs")
    health = target.integrity()
    if not health["ok"]:
        raise IntegrityCheckError("migrated database failed integrity checks")
    legacy_columns = {
        "facts": (
            "fact_id,content,category,tags,trust,status,as_of,superseded_by,"
            "source_kind,source_ref,created_at,updated_at"
        ),
        "entities": "entity_id,name,name_key,entity_type,created_at",
        "fact_entities": "fact_id,entity_id",
        "folders": "folder_id,path,label,enabled,created_at",
        "files": "file_id,path,folder,name,sha256,size,mtime,indexed_at",
        "harnesses": "harness_id,key,display_name,session_root,mcp_mounted,harvest_ok,notes,created_at,updated_at",
        "audit": "audit_id,action,fact_id,actor,detail,created_at",
    }
    for table, columns in legacy_columns.items():
        # Identifiers are fixed above; no user-supplied SQL is interpolated.
        query = f"SELECT {columns} FROM {table} ORDER BY {columns}"  # noqa: S608
        before_rows = [tuple(row) for row in source.execute(query)]
        after_rows = [tuple(row) for row in target.connection.execute(query)]
        if before_rows != after_rows:
            raise IntegrityCheckError(f"migration changed legacy values in {table}")
    return {"tables": comparison, "statuses": after_status, "integrity": health, "legacy_values_preserved": True}


def migrate_apply(data_dir: str | Path, db_path: str | Path) -> dict[str, Any]:
    data = require_absolute(data_dir, name="data dir")
    source_path = require_absolute(db_path, name="database")
    plan = migration_plan(source_path)
    if plan["action"] == "none":
        return {**plan, "applied": False}
    if plan["blockers"]:
        raise ConflictError("migration plan has blockers", details={"blockers": plan["blockers"]})
    manager = BackupManager(data)
    lock_path = source_path.with_suffix(source_path.suffix + ".write.lock")
    temp: Path | None = None
    with exclusive_file_lock(lock_path):
        # The preflight above is advisory. Another writer or migrator may have
        # changed the database before this lock was acquired.
        if schema_version(source_path) == SCHEMA_VERSION:
            return {**migration_plan(source_path), "applied": False}
        source = sqlite3.connect(f"{source_path.as_uri()}?mode=rw", uri=True, isolation_level=None)
        source.row_factory = sqlite3.Row
        try:
            source.execute("PRAGMA foreign_keys = ON")
            source.execute("PRAGMA busy_timeout = 30000")
            # Also reserve SQLite's writer lock: already-running v1 processes
            # predate the cross-platform With. lock. Stop old clients before
            # applying so none retains a handle to the replaced database.
            source.execute("BEGIN IMMEDIATE")
            source.execute("SELECT COUNT(*) FROM facts").fetchone()
            integrity = [str(row[0]) for row in source.execute("PRAGMA integrity_check")]
            foreign_keys = [tuple(row) for row in source.execute("PRAGMA foreign_key_check")]
            if integrity != ["ok"] or foreign_keys:
                raise IntegrityCheckError("source snapshot failed integrity checks")
            plan = _plan_v1_snapshot(source, {"ok": True, "integrity": integrity, "foreign_key_violations": []})
            if plan["blockers"]:
                raise ConflictError("migration plan has blockers", details={"blockers": plan["blockers"]})
            # Backing up a connection with an active write transaction can
            # deadlock. A separate reader sees the same committed snapshot
            # because the source writer reservation prevents further commits.
            backup_reader = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)
            try:
                backup = manager.create_from_connection(backup_reader, schema_version=1, kind="pre-migration")
            finally:
                backup_reader.close()
            backup_path = Path(str(backup["path"]))
            library_uid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"with-library:{sha256_file(backup_path)}"))
            fd, temp_name = tempfile.mkstemp(prefix=f".{source_path.name}.v2-", suffix=".tmp", dir=source_path.parent)
            os.close(fd)
            temp = Path(temp_name)
            temp.unlink()
            target = MemoryStore(temp, mode="create")
            try:
                _replace_library_identity(target, library_uid)
                _copy_entities(source, target)
                facts = source.execute("SELECT * FROM facts ORDER BY fact_id").fetchall()
                scope_issues: list[tuple[int, list[str]]] = []
                for row in facts:
                    scope, issues = scope_from_tags(str(row["tags"] or ""))
                    if issues:
                        scope_issues.append((int(row["fact_id"]), issues))
                    fact_uid = str(uuid.uuid5(uuid.UUID(library_uid), f"legacy-fact:{int(row['fact_id'])}"))
                    # Migration preserves historical values verbatim. The new
                    # fact API intentionally normalizes input and timestamps.
                    with target.write_transaction():
                        scope_id, _ = target._ensure_scope(scope)
                        target.connection.execute(
                            """
                            INSERT INTO facts(
                                fact_id, fact_uid, content, normalized_content_hash, category, tags,
                                trust, status, as_of, source_kind, source_ref, scope_fingerprint,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                row["fact_id"],
                                fact_uid,
                                row["content"],
                                normalized_content_hash(row["content"]),
                                row["category"],
                                row["tags"],
                                row["trust"],
                                row["status"],
                                row["as_of"],
                                row["source_kind"],
                                row["source_ref"],
                                scope.fingerprint,
                                row["created_at"],
                                row["updated_at"],
                            ),
                        )
                        target.connection.execute(
                            "INSERT INTO fact_scopes(fact_id, scope_id) VALUES (?, ?)",
                            (row["fact_id"], scope_id),
                        )
                        target._index_search_terms(int(row["fact_id"]), str(row["content"]), str(row["tags"]))
                with target.write_transaction():
                    target.connection.execute("DELETE FROM fact_entities")
                    for row in source.execute(
                        "SELECT fact_id, entity_id FROM fact_entities ORDER BY fact_id, entity_id"
                    ).fetchall():
                        target.connection.execute(
                            "INSERT INTO fact_entities(fact_id, entity_id) VALUES (?, ?)",
                            tuple(row),
                        )
                    for row in facts:
                        if row["superseded_by"] is not None:
                            target.connection.execute(
                                "UPDATE facts SET superseded_by = ? WHERE fact_id = ?",
                                (int(row["superseded_by"]), int(row["fact_id"])),
                            )
                            target.connection.execute(
                                """
                                INSERT INTO supersessions(
                                    supersession_uid, old_fact_id, new_fact_id, reason, created_at
                                ) VALUES (?, ?, ?, 'migrated legacy relation', ?)
                                """,
                                (
                                    str(
                                        uuid.uuid5(
                                            uuid.UUID(library_uid),
                                            f"legacy-supersession:{int(row['fact_id'])}:{int(row['superseded_by'])}",
                                        )
                                    ),
                                    int(row["fact_id"]),
                                    int(row["superseded_by"]),
                                    str(row["updated_at"]),
                                ),
                            )
                    for fact_id, issues in scope_issues:
                        target.connection.execute(
                            """
                            INSERT INTO migration_issues(issue_code, object_type, object_id, detail, created_at)
                            VALUES ('AMBIGUOUS_SCOPE_TAG', 'fact', ?, ?, ?)
                            """,
                            (str(fact_id), json.dumps(issues, ensure_ascii=False), utc_now()),
                        )
                _copy_sources_and_files(source, target, library_uid)
                with target.write_transaction():
                    target.connection.execute("DELETE FROM audit")
                    for row in source.execute("SELECT * FROM audit ORDER BY audit_id").fetchall():
                        target.connection.execute(
                            """
                            INSERT INTO audit(audit_id, action, fact_id, actor, detail, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            tuple(row),
                        )
                    baseline = int(
                        target.connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM change_events").fetchone()[0]
                    )
                    target.set_meta("migration_backup", str(backup_path))
                    target.set_meta("migration_at", utc_now())
                    target.set_meta("migration_change_sequence", str(baseline))
                    target.set_meta("v2_write_count", "0")
                validation = _validate_copy(source, target)
                target.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                target.close()
            source.rollback()
        except Exception:
            with suppress(sqlite3.Error):
                source.rollback()
            if temp is not None:
                temp.unlink(missing_ok=True)
                Path(str(temp) + "-wal").unlink(missing_ok=True)
                Path(str(temp) + "-shm").unlink(missing_ok=True)
                temp.with_suffix(temp.suffix + ".write.lock").unlink(missing_ok=True)
            raise
        finally:
            source.close()

        assert temp is not None
        os.replace(temp, source_path)
        for suffix in ("-wal", "-shm"):
            Path(str(source_path) + suffix).unlink(missing_ok=True)
            Path(str(temp) + suffix).unlink(missing_ok=True)
        temp.with_suffix(temp.suffix + ".write.lock").unlink(missing_ok=True)
        if os.name != "nt":
            os.chmod(source_path, 0o600)
            dir_fd = os.open(source_path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

        configuration_error = ""
        try:
            cfg = load_config(data)
            if cfg is not None:
                cfg.schema_version = SCHEMA_VERSION
                cfg.tier = "unified"
                save_config(cfg)
        except Exception as exc:
            configuration_error = type(exc).__name__
        return {
            "applied": True,
            "committed": True,
            "configuration": "pending" if configuration_error else "saved",
            "configuration_error": configuration_error,
            "from_schema": 1,
            "to_schema": SCHEMA_VERSION,
            "library_uid": library_uid,
            "backup": backup,
            "validation": validation,
            "warnings": plan["warnings"],
        }


def rollback_plan(db_path: str | Path) -> dict[str, Any]:
    path = require_absolute(db_path, name="database")
    store = MemoryStore(path, mode="ro")
    try:
        backup = store._meta("migration_backup", "")
        writes = int(store._meta("v2_write_count", "0"))
        baseline = int(store._meta("migration_change_sequence", "0"))
        if not backup:
            raise ConflictError("database has no v1 migration backup")
        return {
            "backup": backup,
            "post_migration_writes": writes,
            "migration_change_sequence": baseline,
            "lossless": writes == 0,
            "requires_incremental_export": writes > 0,
        }
    finally:
        store.close()


def export_incremental_before_rollback(store: MemoryStore, output: str | Path) -> dict[str, Any]:
    target = require_absolute(output, name="incremental export")
    baseline = int(store._meta("migration_change_sequence", "0"))
    events = [
        dict(row)
        for row in store.connection.execute(
            "SELECT * FROM change_events WHERE sequence > ? ORDER BY sequence", (baseline,)
        ).fetchall()
    ]
    fact_uids = {str(event["object_uid"]) for event in events if event["object_type"] == "fact"}
    facts = []
    for fact_uid in sorted(fact_uids):
        fact = store.get_fact_by_uid(fact_uid)
        if fact:
            facts.append(fact.to_dict())
    candidate_uids = [str(row[0]) for row in store.connection.execute("SELECT candidate_uid FROM candidates")]
    candidates = [store.get_candidate(uid).to_dict() for uid in candidate_uids]
    v2_state = []
    state_tables = (
        "principals",
        "principal_grants",
        "sources",
        "source_files",
        "scan_runs",
        "fact_sources",
        "fact_scopes",
    )
    for table in state_tables:
        for row in store.connection.execute(f"SELECT * FROM {table}"):  # noqa: S608 - fixed table names
            v2_state.append({"type": "v2_state", "table": table, "value": dict(row)})
    lines = [
        json.dumps({"type": "metadata", "format": "with-v2-rollback-increment-v1"}, ensure_ascii=False),
        *(json.dumps({"type": "change_event", "value": item}, ensure_ascii=False) for item in events),
        *(json.dumps({"type": "fact", "value": item}, ensure_ascii=False) for item in facts),
        *(json.dumps({"type": "candidate", "value": item}, ensure_ascii=False) for item in candidates),
        *(json.dumps(item, ensure_ascii=False) for item in v2_state),
    ]
    atomic_write_text(target, "\n".join(lines) + "\n", mode=0o600)
    return {
        "path": str(target),
        "events": len(events),
        "facts": len(facts),
        "candidates": len(candidates),
        "v2_state_rows": len(v2_state),
    }


def migrate_rollback(
    data_dir: str | Path,
    db_path: str | Path,
    *,
    confirm_loss: bool = False,
    incremental_export: str | Path | None = None,
) -> dict[str, Any]:
    data = require_absolute(data_dir, name="data dir")
    path = require_absolute(db_path, name="database")
    plan = rollback_plan(path)
    if plan["post_migration_writes"] > 0 and not confirm_loss:
        raise ConfirmationRequiredError(
            "rollback would discard post-migration v2 writes",
            details=plan,
        )
    manager = BackupManager(data)
    lock_path = path.with_suffix(path.suffix + ".write.lock")
    with exclusive_file_lock(lock_path):
        plan = rollback_plan(path)
        if plan["post_migration_writes"] > 0 and not confirm_loss:
            raise ConfirmationRequiredError("rollback would discard post-migration v2 writes", details=plan)
        store = MemoryStore(path, mode="rw-existing")
        try:
            # The migration writer lock is already held across this sequence.
            safety_snapshot = manager.create_from_connection(
                store.connection, schema_version=store.schema_version, kind="pre-rollback"
            )
            exported = None
            if plan["post_migration_writes"] > 0:
                if incremental_export is None:
                    raise ValidationError("incremental export path is required for lossy rollback")
                exported = export_incremental_before_rollback(store, incremental_export)
        finally:
            store.close()
        verified = manager.verify(plan["backup"])
        if not verified["ok"] or not verified["expected_sha256"] or verified["database"]["schema_version"] != 1:
            raise IntegrityCheckError("migration rollback backup is invalid or not schema v1")
        restored = atomic_restore_database(path, plan["backup"])
        configuration_error = ""
        try:
            cfg = load_config(data)
            if cfg is not None:
                cfg.schema_version = 1
                save_config(cfg)
        except Exception as exc:
            configuration_error = type(exc).__name__
        return {
            "rolled_back": True,
            "committed": True,
            "configuration": "pending" if configuration_error else "saved",
            "configuration_error": configuration_error,
            "restored": restored,
            "safety_snapshot": safety_snapshot,
            "incremental_export": exported,
        }
