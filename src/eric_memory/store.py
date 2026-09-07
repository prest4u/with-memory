"""Owned SQLite store with explicit connection modes and transactional writes."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from .entities import collect_entities, is_pure_name, normalize_names
from .errors import (
    ConflictError,
    DatabaseNotFoundError,
    IntegrityCheckError,
    MigrationRequiredError,
    NotFoundError,
    PermissionRequiredError,
    SchemaTooNewError,
    ValidationError,
)
from .locking import exclusive_file_lock
from .models import AuditEvent, Candidate, Fact, Harness
from .paths import PathError, require_absolute
from .permissions import (
    ALL_CAPABILITIES,
    DEFAULT_HARNESS_CAPABILITIES,
    FACT_SEARCH,
    LEGACY_CAPABILITIES,
)
from .schema import SCHEMA_V2, SCHEMA_VERSION, SEARCH_RANK_VERSION
from .scopes import ScopeSpec, scope_from_tags, validate_scope
from .security import harden_private_path
from .transactions import immediate_transaction

ConnectionMode = Literal["ro", "rw-existing", "create"]
LATIN_TERM_RE = re.compile(r"[A-Za-z0-9]+")
CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
SQLITE_PARAMETER_CHUNK = 800


def _chunks(values: list[str] | set[str], size: int = SQLITE_PARAMETER_CHUNK) -> Iterator[list[str]]:
    ordered = list(values)
    for index in range(0, len(ordered), size):
        yield ordered[index : index + size]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def clamp_trust(value: float) -> float:
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValidationError("confidence/trust must be between 0 and 1")
    return number


def normalize_content(content: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", content).casefold().split())


def normalized_content_hash(content: str) -> str:
    return hashlib.sha256(normalize_content(content).encode("utf-8")).hexdigest()


def _last_insert_id(cursor: sqlite3.Cursor) -> int:
    value = cursor.lastrowid
    if value is None:
        raise IntegrityCheckError("SQLite did not return an inserted row ID")
    return int(value)


def search_terms(content: str) -> set[tuple[str, str]]:
    normalized = unicodedata.normalize("NFKC", content).casefold()
    terms: set[tuple[str, str]] = {(match.group(0), "latin") for match in LATIN_TERM_RE.finditer(normalized)}
    for run in CJK_RUN_RE.findall(normalized):
        terms.update((char, "cjk1") for char in run)
        terms.update((run[index : index + 2], "cjk2") for index in range(len(run) - 1))
    return terms


def _safe_metadata(value: dict[str, Any] | None) -> str:
    data = value or {}
    forbidden = {"content", "body", "text", "path", "source_locator", "hash"}
    cleaned = {key: item for key, item in data.items() if key.casefold() not in forbidden}
    return json.dumps(cleaned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class MemoryStore:
    """The only SQLite write path used by With."""

    def __init__(self, db_path: str | Path, *, mode: ConnectionMode = "rw-existing") -> None:
        self.db_path = require_absolute(db_path, name="db path")
        if self.db_path.suffix == "" and str(self.db_path).endswith("~"):
            raise PathError("db path must not contain '~'")
        if mode not in {"ro", "rw-existing", "create"}:
            raise ValueError(f"invalid connection mode: {mode}")
        self.mode: ConnectionMode = mode
        self.write_lock_path = self.db_path.with_suffix(self.db_path.suffix + ".write.lock")
        self._lock = threading.RLock()
        self._maintenance_locked = False

        existed = self.db_path.is_file() and self.db_path.stat().st_size > 0
        if mode != "create" and not existed:
            raise DatabaseNotFoundError(
                f"memory database not found: {self.db_path}",
                details={"db_path": str(self.db_path)},
            )
        if mode == "create":
            self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            harden_private_path(self.db_path.parent, directory=True)
            sqlite_mode = "rw" if existed else "rwc"
        else:
            sqlite_mode = "ro" if mode == "ro" else "rw"

        self._conn = sqlite3.connect(
            f"{self.db_path.as_uri()}?mode={sqlite_mode}",
            uri=True,
            check_same_thread=False,
            timeout=30.0,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

        if not existed:
            self._configure_writer()
            self._init_schema_v2()
            self._harden_permissions()
        else:
            version = self._read_schema_version()
            if version > SCHEMA_VERSION:
                self._conn.close()
                raise SchemaTooNewError(f"database schema v{version} is newer than supported v{SCHEMA_VERSION}")
            if mode == "create" and version < SCHEMA_VERSION:
                self._conn.close()
                raise MigrationRequiredError(f"database schema v{version} requires `eric-memory migrate --apply`")
            if mode == "ro":
                self._conn.execute("PRAGMA query_only = ON")
            else:
                self._configure_writer()
        self.schema_version = self._read_schema_version()

    def _configure_writer(self) -> None:
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._conn.execute("PRAGMA synchronous = FULL")
        self._conn.execute("PRAGMA journal_mode = WAL")

    def _harden_permissions(self) -> None:
        harden_private_path(self.db_path.parent, directory=True)
        harden_private_path(self.db_path, directory=False)

    def _read_schema_version(self) -> int:
        try:
            row = self._conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        except sqlite3.DatabaseError as exc:
            raise IntegrityCheckError("file is not a valid With. database") from exc
        if row is None:
            raise IntegrityCheckError("database is missing schema metadata")
        try:
            return int(row["value"])
        except (TypeError, ValueError) as exc:
            raise IntegrityCheckError("database schema version is invalid") from exc

    def _init_schema_v2(self) -> None:
        now = utc_now()
        library_uid = str(uuid.uuid4())
        device_uid = str(uuid.uuid4())
        self._conn.executescript(SCHEMA_V2)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            meta = {
                "schema_version": str(SCHEMA_VERSION),
                "search_rank_version": SEARCH_RANK_VERSION,
                "created_at": now,
                "v2_write_count": "0",
                "migration_backup": "",
            }
            self._conn.executemany("INSERT INTO schema_meta(key, value) VALUES (?, ?)", meta.items())
            self._conn.execute(
                "INSERT INTO libraries(library_uid, created_at) VALUES (?, ?)",
                (library_uid, now),
            )
            self._conn.execute(
                "INSERT INTO devices(device_uid, library_uid, label, created_at) VALUES (?, ?, ?, ?)",
                (device_uid, library_uid, "local", now),
            )
            user_scope_uid = str(uuid.uuid4())
            cur = self._conn.execute(
                """
                INSERT INTO scopes(scope_uid, scope_kind, scope_key, fingerprint, created_at)
                VALUES (?, 'user', '', 'user:', ?)
                """,
                (user_scope_uid, now),
            )
            user_scope_id = _last_insert_id(cur)
            self._conn.execute(
                """
                INSERT INTO principals(
                    principal_uid, key, display_name, enabled, local_admin, created_at, updated_at
                ) VALUES (?, 'local', 'Local administrator', 1, 1, ?, ?)
                """,
                (str(uuid.uuid4()), now, now),
            )
            legacy_cur = self._conn.execute(
                """
                INSERT INTO principals(
                    principal_uid, key, display_name, enabled, local_admin, created_at, updated_at
                ) VALUES (?, 'legacy', 'Legacy MCP client', 1, 0, ?, ?)
                """,
                (str(uuid.uuid4()), now, now),
            )
            legacy_id = _last_insert_id(legacy_cur)
            for capability in LEGACY_CAPABILITIES:
                scope_id = user_scope_id if capability == FACT_SEARCH else None
                self._conn.execute(
                    """
                    INSERT INTO principal_grants(principal_id, capability, scope_id, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (legacy_id, capability, scope_id, now),
                )
            self._conn.execute(
                """
                INSERT INTO projection_state(projection_key, status, updated_at)
                VALUES ('obsidian', 'disabled', ?)
                """,
                (now,),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _require_v2(self) -> None:
        if self.schema_version < SCHEMA_VERSION:
            raise MigrationRequiredError(
                f"database schema v{self.schema_version} requires migration to v{SCHEMA_VERSION}"
            )

    def _require_write(self) -> None:
        if self.mode == "ro":
            raise PermissionError("store is open read-only")
        self._require_v2()

    def write_transaction(self) -> AbstractContextManager[None]:
        self._require_write()
        return immediate_transaction(self._conn, self.write_lock_path, self._lock, lock_context=self.maintenance_lock())

    @contextmanager
    def maintenance_lock(self) -> Iterator[None]:
        """Serialize maintenance and backups; nested operations share this store's lock."""
        with self._lock:
            if self._maintenance_locked:
                yield
                return
            with exclusive_file_lock(self.write_lock_path):
                self._maintenance_locked = True
                try:
                    yield
                finally:
                    self._maintenance_locked = False

    def _meta(self, key: str, default: str = "") -> str:
        row = self._conn.execute("SELECT value FROM schema_meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_meta(self, key: str, value: str) -> None:
        self._require_write()
        self._conn.execute(
            """
            INSERT INTO schema_meta(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    @property
    def library_uid(self) -> str:
        self._require_v2()
        row = self._conn.execute("SELECT library_uid FROM libraries LIMIT 1").fetchone()
        assert row is not None
        return str(row["library_uid"])

    @property
    def device_uid(self) -> str:
        self._require_v2()
        row = self._conn.execute("SELECT device_uid FROM devices LIMIT 1").fetchone()
        assert row is not None
        return str(row["device_uid"])

    def _principal_uid(self, principal_key: str) -> str:
        row = self._conn.execute("SELECT principal_uid FROM principals WHERE key = ?", (principal_key,)).fetchone()
        return str(row["principal_uid"]) if row else ""

    def _audit(
        self,
        action: str,
        *,
        fact_id: int | None = None,
        actor: str = "local",
        detail: str = "",
        operation_uid: str,
        object_type: str = "fact",
        object_uid: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO audit(action, fact_id, actor, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (action, fact_id, actor, detail[:300], utc_now()),
        )
        self._conn.execute(
            """
            INSERT INTO audit_events(
                event_uid, operation_uid, principal_uid, action, object_type,
                object_uid, outcome, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'success', ?, ?)
            """,
            (
                str(uuid.uuid4()),
                operation_uid,
                self._principal_uid(actor),
                action,
                object_type,
                object_uid,
                _safe_metadata(metadata),
                utc_now(),
            ),
        )

    def _change(
        self,
        action: str,
        *,
        object_type: str,
        object_uid: str,
        operation_uid: str,
        metadata: dict[str, Any] | None = None,
        count_as_v2_write: bool = True,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO change_events(
                event_uid, library_uid, device_uid, operation_uid, object_type,
                object_uid, action, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                self.library_uid,
                self.device_uid,
                operation_uid,
                object_type,
                object_uid,
                action,
                _safe_metadata(metadata),
                utc_now(),
            ),
        )
        # Rollback accounting is performed once per committed transaction by
        # write_transaction(). The argument remains for v1 API compatibility.
        del count_as_v2_write

    def _known_entity_names(self) -> list[str]:
        rows = self._conn.execute("SELECT name FROM entities ORDER BY entity_id").fetchall()
        return [str(row["name"]) for row in rows]

    def _resolve_entity(self, name: str, entity_type: str = "unknown") -> int | None:
        if not is_pure_name(name):
            return None
        key = unicodedata.normalize("NFKC", name).casefold()
        row = self._conn.execute("SELECT entity_id FROM entities WHERE name_key = ?", (key,)).fetchone()
        if row:
            return int(row["entity_id"])
        cur = self._conn.execute(
            "INSERT INTO entities(name, name_key, entity_type, created_at) VALUES (?, ?, ?, ?)",
            (name, key, entity_type, utc_now()),
        )
        return _last_insert_id(cur)

    def _link_entities(self, fact_id: int, names: list[str]) -> list[str]:
        linked: list[str] = []
        for name in normalize_names(names):
            entity_id = self._resolve_entity(name)
            if entity_id is None:
                continue
            self._conn.execute(
                "INSERT OR IGNORE INTO fact_entities(fact_id, entity_id) VALUES (?, ?)",
                (fact_id, entity_id),
            )
            linked.append(name)
        return linked

    def add_entity_alias(self, entity_name: str, alias: str) -> None:
        operation_uid = str(uuid.uuid4())
        with self.write_transaction():
            entity_id = self._resolve_entity(entity_name)
            if entity_id is None or not is_pure_name(alias):
                raise ValidationError("entity and alias must be names, not sentences")
            self._conn.execute(
                """
                INSERT INTO entity_aliases(entity_id, alias, alias_key, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(alias_key) DO UPDATE SET entity_id = excluded.entity_id, alias = excluded.alias
                """,
                (entity_id, alias, unicodedata.normalize("NFKC", alias).casefold(), utc_now()),
            )
            self._change(
                "alias_upsert",
                object_type="entity",
                object_uid=str(entity_id),
                operation_uid=operation_uid,
            )

    def _entities_for(self, fact_id: int) -> list[str]:
        rows = self._conn.execute(
            """
            SELECT e.name FROM entities e
            JOIN fact_entities fe ON fe.entity_id = e.entity_id
            WHERE fe.fact_id = ? ORDER BY e.name
            """,
            (fact_id,),
        ).fetchall()
        return [str(row["name"]) for row in rows]

    def _scope_for_fact(self, fact_id: int, fallback_tags: str = "") -> dict[str, str]:
        if self.schema_version < 2:
            return scope_from_tags(fallback_tags)[0].to_dict()
        row = self._conn.execute(
            """
            SELECT s.scope_kind, s.scope_key FROM scopes s
            JOIN fact_scopes fs ON fs.scope_id = s.scope_id
            WHERE fs.fact_id = ? ORDER BY s.scope_id LIMIT 1
            """,
            (fact_id,),
        ).fetchone()
        if not row:
            return {"kind": "user", "key": ""}
        return {"kind": str(row["scope_kind"]), "key": str(row["scope_key"])}

    def _source_count_for_fact(self, fact_id: int) -> int:
        if self.schema_version < 2:
            return 0
        row = self._conn.execute("SELECT COUNT(*) AS count FROM fact_sources WHERE fact_id = ?", (fact_id,)).fetchone()
        return int(row["count"])

    def _row_to_fact(self, row: sqlite3.Row) -> Fact:
        keys = set(row.keys())
        fact_id = int(row["fact_id"])
        return Fact(
            fact_id=fact_id,
            content=str(row["content"]),
            category=str(row["category"]),
            tags=str(row["tags"]),
            trust=float(row["trust"]),
            status=str(row["status"]),
            as_of=str(row["as_of"]),
            superseded_by=row["superseded_by"],
            source_kind=str(row["source_kind"]),
            source_ref=str(row["source_ref"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            entities=self._entities_for(fact_id),
            fact_uid=str(row["fact_uid"]) if "fact_uid" in keys else "",
            scope=self._scope_for_fact(fact_id, str(row["tags"])),
            source_count=self._source_count_for_fact(fact_id),
        )

    def get_fact(self, fact_id: int) -> Fact | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM facts WHERE fact_id = ?", (fact_id,)).fetchone()
            return self._row_to_fact(row) if row else None

    def get_fact_by_uid(self, fact_uid: str) -> Fact | None:
        self._require_v2()
        with self._lock:
            row = self._conn.execute("SELECT * FROM facts WHERE fact_uid = ?", (fact_uid,)).fetchone()
            return self._row_to_fact(row) if row else None

    def _ensure_scope(self, scope: ScopeSpec) -> tuple[int, str]:
        row = self._conn.execute(
            "SELECT scope_id, scope_uid FROM scopes WHERE fingerprint = ?", (scope.fingerprint,)
        ).fetchone()
        if row:
            return int(row["scope_id"]), str(row["scope_uid"])
        scope_uid = str(uuid.uuid4())
        cur = self._conn.execute(
            """
            INSERT INTO scopes(scope_uid, scope_kind, scope_key, fingerprint, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (scope_uid, scope.kind, scope.key, scope.fingerprint, utc_now()),
        )
        return _last_insert_id(cur), scope_uid

    def get_scope(self, scope: ScopeSpec) -> sqlite3.Row | None:
        self._require_v2()
        row: sqlite3.Row | None = self._conn.execute(
            "SELECT * FROM scopes WHERE fingerprint = ?", (scope.fingerprint,)
        ).fetchone()
        return row

    def _index_search_terms(self, fact_id: int, content: str, tags: str) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO fact_search_terms(fact_id, term, kind) VALUES (?, ?, ?)",
            ((fact_id, term, kind) for term, kind in search_terms(f"{content} {tags}")),
        )

    def _source_row(self, source_uid: str) -> sqlite3.Row:
        row: sqlite3.Row | None = self._conn.execute(
            "SELECT * FROM sources WHERE source_uid = ?", (source_uid,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"source_uid {source_uid} not found")
        return row

    def _link_source(self, fact_id: int, source_uid: str, locator: str) -> None:
        source = self._source_row(source_uid)
        self._conn.execute(
            """
            INSERT OR IGNORE INTO fact_sources(fact_id, source_id, source_locator, linked_at)
            VALUES (?, ?, ?, ?)
            """,
            (fact_id, int(source["source_id"]), locator, utc_now()),
        )

    def _insert_fact_no_tx(
        self,
        content: str,
        *,
        category: str,
        tags: str,
        trust: float,
        as_of: str,
        entities: list[str],
        source_kind: str,
        source_ref: str,
        source_uid: str | None,
        source_locator: str,
        actor: str,
        status: str,
        created_at: str,
        scope: ScopeSpec,
        operation_uid: str,
        fact_uid: str | None = None,
        fact_id: int | None = None,
        count_as_v2_write: bool = True,
    ) -> tuple[Fact, bool]:
        digest = normalized_content_hash(content)
        if source_ref:
            existing = self._conn.execute(
                "SELECT * FROM facts WHERE source_kind = ? AND source_ref = ?",
                (source_kind, source_ref),
            ).fetchone()
            if existing:
                existing_id = int(existing["fact_id"])
                if source_uid:
                    self._link_source(existing_id, source_uid, source_locator)
                return self._row_to_fact(existing), False
        if status == "active":
            duplicate = self._conn.execute(
                """
                SELECT * FROM facts WHERE normalized_content_hash = ?
                AND scope_fingerprint = ? AND status = 'active'
                """,
                (digest, scope.fingerprint),
            ).fetchone()
            if duplicate:
                duplicate_id = int(duplicate["fact_id"])
                if source_uid:
                    self._link_source(duplicate_id, source_uid, source_locator)
                return self._row_to_fact(duplicate), False

        scope_id, _ = self._ensure_scope(scope)
        uid = fact_uid or str(uuid.uuid4())
        values: list[Any] = [
            uid,
            content,
            digest,
            category or "general",
            tags or "",
            clamp_trust(trust),
            status,
            as_of,
            source_kind,
            source_ref or "",
            scope.fingerprint,
            created_at,
            utc_now(),
        ]
        if fact_id is None:
            cur = self._conn.execute(
                """
                INSERT INTO facts(
                    fact_uid, content, normalized_content_hash, category, tags, trust, status,
                    as_of, source_kind, source_ref, scope_fingerprint, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            new_id = _last_insert_id(cur)
        else:
            self._conn.execute(
                """
                INSERT INTO facts(
                    fact_id, fact_uid, content, normalized_content_hash, category, tags, trust,
                    status, as_of, source_kind, source_ref, scope_fingerprint, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [fact_id, *values],
            )
            new_id = int(fact_id)
        self._conn.execute("INSERT INTO fact_scopes(fact_id, scope_id) VALUES (?, ?)", (new_id, scope_id))
        known = self._known_entity_names()
        self._link_entities(new_id, collect_entities(content, entities, known))
        self._index_search_terms(new_id, content, tags)
        if source_uid:
            self._link_source(new_id, source_uid, source_locator)
        self._audit(
            "add",
            fact_id=new_id,
            actor=actor,
            detail=source_kind,
            operation_uid=operation_uid,
            object_type="fact",
            object_uid=uid,
            metadata={"source_kind": source_kind, "scope": scope.fingerprint},
        )
        self._change(
            "created",
            object_type="fact",
            object_uid=uid,
            operation_uid=operation_uid,
            metadata={"status": status, "scope": scope.fingerprint},
            count_as_v2_write=count_as_v2_write,
        )
        row = self._conn.execute("SELECT * FROM facts WHERE fact_id = ?", (new_id,)).fetchone()
        assert row is not None
        return self._row_to_fact(row), True

    def add_fact_with_result(self, content: str, **kwargs: Any) -> tuple[Fact, bool]:
        value = content.strip()
        if not value:
            raise ValidationError("content must not be empty")
        status = str(kwargs.pop("status", "active"))
        if status not in {"active", "deprecated"}:
            raise ValidationError(f"invalid status: {status}")
        tags = str(kwargs.pop("tags", ""))
        scope = kwargs.pop("scope", None) or scope_from_tags(tags)[0]
        op = str(kwargs.pop("operation_uid", None) or uuid.uuid4())
        with self.write_transaction():
            result = self._insert_fact_no_tx(
                value,
                category=str(kwargs.pop("category", "general")),
                tags=tags,
                trust=float(kwargs.pop("trust", 0.5)),
                as_of=str(kwargs.pop("as_of", None) or today_utc()).strip(),
                entities=list(kwargs.pop("entities", None) or []),
                source_kind=str(kwargs.pop("source_kind", "manual")),
                source_ref=str(kwargs.pop("source_ref", "")),
                source_uid=kwargs.pop("source_uid", None),
                source_locator=str(kwargs.pop("source_locator", "")),
                actor=str(kwargs.pop("actor", "local")),
                status=status,
                created_at=str(kwargs.pop("created_at", None) or utc_now()),
                scope=scope,
                operation_uid=op,
                fact_uid=kwargs.pop("fact_uid", None),
                fact_id=kwargs.pop("fact_id", None),
                count_as_v2_write=bool(kwargs.pop("count_as_v2_write", True)),
            )
            if kwargs:
                raise TypeError(f"unexpected add arguments: {', '.join(sorted(kwargs))}")
            return result

    def add_fact(self, content: str, **kwargs: Any) -> Fact:
        return self.add_fact_with_result(content, **kwargs)[0]

    def _assert_valid_supersession(self, old_fact_id: int, new_fact_id: int) -> None:
        if old_fact_id == new_fact_id:
            raise ValidationError("a fact cannot supersede itself")
        old = self._conn.execute("SELECT status FROM facts WHERE fact_id = ?", (old_fact_id,)).fetchone()
        if old is None:
            raise NotFoundError(f"fact_id {old_fact_id} not found")
        if str(old["status"]) != "active":
            raise ConflictError(f"fact_id {old_fact_id} is not active")
        successor = self._conn.execute("SELECT status FROM facts WHERE fact_id = ?", (new_fact_id,)).fetchone()
        if successor is None:
            raise NotFoundError(f"superseded_by {new_fact_id} not found")
        if str(successor["status"]) != "active":
            raise ConflictError("successor must be active")
        current: int | None = new_fact_id
        seen: set[int] = set()
        while current is not None:
            if current == old_fact_id:
                raise ConflictError("supersession would create a cycle")
            if current in seen:
                raise IntegrityCheckError("existing supersession graph contains a cycle")
            seen.add(current)
            row = self._conn.execute("SELECT superseded_by FROM facts WHERE fact_id = ?", (current,)).fetchone()
            current = int(row["superseded_by"]) if row and row["superseded_by"] is not None else None

    def _deprecate_no_tx(
        self,
        fact_id: int,
        *,
        superseded_by: int | None,
        reason: str,
        actor: str,
        operation_uid: str,
        count_as_v2_write: bool = True,
    ) -> Fact:
        row = self._conn.execute("SELECT * FROM facts WHERE fact_id = ?", (fact_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"fact_id {fact_id} not found")
        if str(row["status"]) == "deprecated":
            if row["superseded_by"] == superseded_by:
                return self._row_to_fact(row)
            raise ConflictError(f"fact_id {fact_id} is already deprecated")
        if superseded_by is not None:
            self._assert_valid_supersession(fact_id, int(superseded_by))
        now = utc_now()
        self._conn.execute(
            "UPDATE facts SET status = 'deprecated', superseded_by = ?, updated_at = ? WHERE fact_id = ?",
            (superseded_by, now, fact_id),
        )
        if superseded_by is not None:
            self._conn.execute(
                """
                INSERT INTO supersessions(
                    supersession_uid, old_fact_id, new_fact_id, reason, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), fact_id, superseded_by, reason, now),
            )
        fact_uid = str(row["fact_uid"])
        self._audit(
            "deprecate",
            fact_id=fact_id,
            actor=actor,
            detail=("superseded" if superseded_by else "deprecated"),
            operation_uid=operation_uid,
            object_type="fact",
            object_uid=fact_uid,
            metadata={
                "superseded_by": superseded_by,
                "reason_code": "provided" if reason else "none",
            },
        )
        self._change(
            "deprecated",
            object_type="fact",
            object_uid=fact_uid,
            operation_uid=operation_uid,
            metadata={"superseded_by": superseded_by},
            count_as_v2_write=count_as_v2_write,
        )
        updated = self._conn.execute("SELECT * FROM facts WHERE fact_id = ?", (fact_id,)).fetchone()
        assert updated is not None
        return self._row_to_fact(updated)

    def deprecate(
        self,
        fact_id: int,
        *,
        superseded_by: int | None = None,
        reason: str = "",
        actor: str = "local",
        operation_uid: str | None = None,
    ) -> Fact:
        op = operation_uid or str(uuid.uuid4())
        with self.write_transaction():
            return self._deprecate_no_tx(
                fact_id,
                superseded_by=superseded_by,
                reason=reason,
                actor=actor,
                operation_uid=op,
            )

    def add_and_supersede(
        self,
        content: str,
        old_fact_id: int,
        *,
        reason: str = "superseded on add",
        operation_uid: str | None = None,
        **kwargs: Any,
    ) -> tuple[Fact, Fact | None, bool]:
        op = operation_uid or str(uuid.uuid4())
        value = content.strip()
        if not value:
            raise ValidationError("content must not be empty")
        tags = str(kwargs.pop("tags", ""))
        scope = kwargs.pop("scope", None) or scope_from_tags(tags)[0]
        actor = str(kwargs.pop("actor", "local"))
        with self.write_transaction():
            old_row = self._conn.execute("SELECT * FROM facts WHERE fact_id = ?", (old_fact_id,)).fetchone()
            if old_row is None:
                raise NotFoundError(f"fact_id {old_fact_id} not found")
            if str(old_row["status"]) != "active":
                raise ConflictError(f"fact_id {old_fact_id} is not active")
            duplicate = self._conn.execute(
                """
                SELECT * FROM facts WHERE normalized_content_hash = ?
                AND scope_fingerprint = ? AND status = 'active'
                """,
                (normalized_content_hash(value), scope.fingerprint),
            ).fetchone()
            if duplicate and int(duplicate["fact_id"]) == int(old_fact_id):
                return self._row_to_fact(duplicate), None, False
            new_fact, created = self._insert_fact_no_tx(
                value,
                category=str(kwargs.pop("category", "general")),
                tags=tags,
                trust=float(kwargs.pop("trust", 0.5)),
                as_of=str(kwargs.pop("as_of", None) or today_utc()),
                entities=list(kwargs.pop("entities", None) or []),
                source_kind=str(kwargs.pop("source_kind", "manual")),
                source_ref=str(kwargs.pop("source_ref", "")),
                source_uid=kwargs.pop("source_uid", None),
                source_locator=str(kwargs.pop("source_locator", "")),
                actor=actor,
                status="active",
                created_at=str(kwargs.pop("created_at", None) or utc_now()),
                scope=scope,
                operation_uid=op,
            )
            if kwargs:
                raise TypeError(f"unexpected add arguments: {', '.join(sorted(kwargs))}")
            old = self._deprecate_no_tx(
                old_fact_id,
                superseded_by=new_fact.fact_id,
                reason=reason,
                actor=actor,
                operation_uid=op,
            )
            return new_fact, old, created

    def counts(self) -> dict[str, int]:
        with self._lock:
            result = {
                "facts": int(self._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]),
                "active": int(self._conn.execute("SELECT COUNT(*) FROM facts WHERE status = 'active'").fetchone()[0]),
                "deprecated": int(
                    self._conn.execute("SELECT COUNT(*) FROM facts WHERE status = 'deprecated'").fetchone()[0]
                ),
                "entities": int(self._conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]),
                "files": int(self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]),
                "harnesses": int(self._conn.execute("SELECT COUNT(*) FROM harnesses").fetchone()[0]),
                "folders": int(self._conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0]),
            }
            if self.schema_version >= 2:
                result["candidates_pending"] = int(
                    self._conn.execute("SELECT COUNT(*) FROM candidates WHERE status = 'pending'").fetchone()[0]
                )
                result["sources"] = int(
                    self._conn.execute("SELECT COUNT(*) FROM sources WHERE status = 'approved'").fetchone()[0]
                )
        return result

    def recent_audit(self, limit: int = 20) -> list[AuditEvent]:
        if not 1 <= int(limit) <= 1000:
            raise ValidationError("audit limit must be between 1 and 1000")
        rows = self._conn.execute("SELECT * FROM audit ORDER BY audit_id DESC LIMIT ?", (limit,)).fetchall()
        return [
            AuditEvent(
                audit_id=int(row["audit_id"]),
                action=str(row["action"]),
                fact_id=row["fact_id"],
                actor=str(row["actor"]),
                detail=str(row["detail"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def history(self, fact_id: int) -> dict[str, Any]:
        fact = self.get_fact(fact_id)
        if fact is None:
            raise NotFoundError(f"fact_id {fact_id} not found")
        chain: list[dict[str, Any]] = []
        current: Fact | None = fact
        seen: set[int] = set()
        while current is not None:
            if current.fact_id in seen:
                raise IntegrityCheckError("supersession graph contains a cycle")
            seen.add(current.fact_id)
            chain.append(current.to_dict())
            current = self.get_fact(int(current.superseded_by)) if current.superseded_by else None
        audit = [item.to_dict() for item in self.recent_audit(1000) if item.fact_id in seen]
        return {"fact_id": fact_id, "chain": chain, "audit": audit}

    # Principals and grants -------------------------------------------------
    def _principal(self, key: str) -> sqlite3.Row | None:
        self._require_v2()
        row: sqlite3.Row | None = self._conn.execute(
            "SELECT * FROM principals WHERE key = ?", (key.strip().lower(),)
        ).fetchone()
        return row

    def ensure_principal(
        self,
        key: str,
        *,
        display_name: str | None = None,
        default_harness_grants: bool = True,
        actor: str = "local",
    ) -> dict[str, Any]:
        normalized = key.strip().lower()
        if not normalized or normalized in {"local", "legacy"}:
            raise ValidationError("principal key is reserved or empty")
        op = str(uuid.uuid4())
        with self.write_transaction():
            row = self._principal(normalized)
            if row is None:
                now = utc_now()
                cur = self._conn.execute(
                    """
                    INSERT INTO principals(
                        principal_uid, key, display_name, enabled, local_admin, created_at, updated_at
                    ) VALUES (?, ?, ?, 1, 0, ?, ?)
                    """,
                    (str(uuid.uuid4()), normalized, display_name or normalized, now, now),
                )
                principal_id = _last_insert_id(cur)
                if default_harness_grants:
                    user_scope = self._conn.execute(
                        "SELECT scope_id FROM scopes WHERE fingerprint = 'user:'"
                    ).fetchone()
                    assert user_scope is not None
                    for capability in DEFAULT_HARNESS_CAPABILITIES:
                        scope_id = int(user_scope["scope_id"]) if capability == FACT_SEARCH else None
                        self._conn.execute(
                            """
                            INSERT INTO principal_grants(principal_id, capability, scope_id, created_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (principal_id, capability, scope_id, now),
                        )
                self._audit(
                    "principal_create",
                    actor=actor,
                    operation_uid=op,
                    object_type="principal",
                    object_uid=normalized,
                )
                self._change(
                    "created",
                    object_type="principal",
                    object_uid=normalized,
                    operation_uid=op,
                )
            elif not bool(row["enabled"]):
                self._conn.execute(
                    "UPDATE principals SET enabled = 1, updated_at = ? WHERE principal_id = ?",
                    (utc_now(), int(row["principal_id"])),
                )
        return self.principal_info(normalized)

    def principal_info(self, key: str) -> dict[str, Any]:
        row = self._principal(key)
        if row is None or not bool(row["enabled"]):
            raise PermissionRequiredError(f"unknown or disabled principal: {key}")
        grants = self._conn.execute(
            """
            SELECT pg.capability, s.scope_kind, s.scope_key
            FROM principal_grants pg
            LEFT JOIN scopes s ON s.scope_id = pg.scope_id
            WHERE pg.principal_id = ? ORDER BY pg.capability, s.scope_kind, s.scope_key
            """,
            (int(row["principal_id"]),),
        ).fetchall()
        return {
            "principal_uid": str(row["principal_uid"]),
            "key": str(row["key"]),
            "display_name": str(row["display_name"]),
            "enabled": bool(row["enabled"]),
            "local_admin": bool(row["local_admin"]),
            "grants": [
                {
                    "capability": str(grant["capability"]),
                    "scope": (
                        {"kind": str(grant["scope_kind"]), "key": str(grant["scope_key"])}
                        if grant["scope_kind"] is not None
                        else None
                    ),
                }
                for grant in grants
            ],
        }

    def list_principals(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT key FROM principals ORDER BY key").fetchall()
        return [self.principal_info(str(row["key"])) for row in rows]

    def grant(
        self,
        principal_key: str,
        capability: str,
        *,
        scope: ScopeSpec | None = None,
        actor: str = "local",
    ) -> dict[str, Any]:
        if capability not in ALL_CAPABILITIES:
            raise ValidationError(f"unknown capability: {capability}")
        op = str(uuid.uuid4())
        with self.write_transaction():
            principal = self._principal(principal_key)
            if principal is None:
                raise NotFoundError(f"principal {principal_key} not found")
            scope_id = self._ensure_scope(scope)[0] if scope else None
            self._conn.execute(
                """
                INSERT OR IGNORE INTO principal_grants(principal_id, capability, scope_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (int(principal["principal_id"]), capability, scope_id, utc_now()),
            )
            self._audit(
                "grant",
                actor=actor,
                operation_uid=op,
                object_type="principal",
                object_uid=principal_key,
                metadata={"capability": capability, "scope": scope.fingerprint if scope else "all"},
            )
            self._change(
                "grant_added",
                object_type="principal",
                object_uid=principal_key,
                operation_uid=op,
                metadata={"capability": capability},
            )
        return self.principal_info(principal_key)

    def revoke_grant(
        self,
        principal_key: str,
        capability: str,
        *,
        scope: ScopeSpec | None = None,
        actor: str = "local",
    ) -> dict[str, Any]:
        op = str(uuid.uuid4())
        with self.write_transaction():
            principal = self._principal(principal_key)
            if principal is None:
                raise NotFoundError(f"principal {principal_key} not found")
            scope_id: int | None = None
            if scope:
                scope_row = self.get_scope(scope)
                if scope_row is None:
                    raise NotFoundError("requested grant scope does not exist")
                scope_id = int(scope_row["scope_id"])
            if scope_id is None:
                self._conn.execute(
                    "DELETE FROM principal_grants WHERE principal_id = ? AND capability = ? AND scope_id IS NULL",
                    (int(principal["principal_id"]), capability),
                )
            else:
                self._conn.execute(
                    "DELETE FROM principal_grants WHERE principal_id = ? AND capability = ? AND scope_id = ?",
                    (int(principal["principal_id"]), capability, scope_id),
                )
            self._audit(
                "grant_revoke",
                actor=actor,
                operation_uid=op,
                object_type="principal",
                object_uid=principal_key,
                metadata={"capability": capability},
            )
            self._change(
                "grant_removed",
                object_type="principal",
                object_uid=principal_key,
                operation_uid=op,
                metadata={"capability": capability},
            )
        return self.principal_info(principal_key)

    def require_capability(
        self,
        principal_key: str,
        capability: str,
        *,
        scope: ScopeSpec | None = None,
    ) -> None:
        principal = self._principal(principal_key)
        if principal is None or not bool(principal["enabled"]):
            raise PermissionRequiredError(f"principal {principal_key!r} is not authorized")
        if bool(principal["local_admin"]):
            return
        rows = self._conn.execute(
            "SELECT scope_id FROM principal_grants WHERE principal_id = ? AND capability = ?",
            (int(principal["principal_id"]), capability),
        ).fetchall()
        if any(row["scope_id"] is None for row in rows):
            return
        # A user-scope request means "all scopes this principal can access",
        # so any scoped grant is sufficient at this aggregate boundary. The
        # retrieval query still filters rows to the exact granted scope IDs.
        if scope is None and rows:
            return
        if scope is not None:
            scope_row = self.get_scope(scope)
            if scope_row and any(int(row["scope_id"]) == int(scope_row["scope_id"]) for row in rows):
                return
        raise PermissionRequiredError(
            f"principal {principal_key!r} requires capability {capability}",
            details={"principal": principal_key, "capability": capability},
        )

    def accessible_scope_ids(self, principal_key: str, capability: str = FACT_SEARCH) -> set[int] | None:
        principal = self._principal(principal_key)
        if principal is None or not bool(principal["enabled"]):
            raise PermissionRequiredError(f"principal {principal_key!r} is not authorized")
        if bool(principal["local_admin"]):
            return None
        rows = self._conn.execute(
            "SELECT scope_id FROM principal_grants WHERE principal_id = ? AND capability = ?",
            (int(principal["principal_id"]), capability),
        ).fetchall()
        if any(row["scope_id"] is None for row in rows):
            return None
        ids = {int(row["scope_id"]) for row in rows if row["scope_id"] is not None}
        if not ids:
            raise PermissionRequiredError(f"principal {principal_key!r} requires capability {capability}")
        return ids

    # Harness compatibility ------------------------------------------------
    def upsert_harness(
        self,
        key: str,
        *,
        display_name: str,
        session_root: str = "",
        mcp_mounted: bool = False,
        harvest_ok: bool = False,
        notes: str = "",
        actor: str = "local",
    ) -> Harness:
        normalized = key.strip().lower()
        if not normalized:
            raise ValidationError("harness key is required")
        if session_root:
            session_root = str(require_absolute(session_root, name="session_root"))
        op = str(uuid.uuid4())
        with self.write_transaction():
            now = utc_now()
            self._conn.execute(
                """
                INSERT INTO harnesses(
                    key, display_name, session_root, mcp_mounted, harvest_ok,
                    notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    display_name = excluded.display_name,
                    session_root = excluded.session_root,
                    mcp_mounted = excluded.mcp_mounted,
                    harvest_ok = excluded.harvest_ok,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized,
                    display_name,
                    session_root,
                    int(mcp_mounted),
                    int(harvest_ok),
                    notes,
                    now,
                    now,
                ),
            )
            self._audit(
                "harness_upsert",
                actor=actor,
                operation_uid=op,
                object_type="harness",
                object_uid=normalized,
            )
            self._change(
                "upserted",
                object_type="harness",
                object_uid=normalized,
                operation_uid=op,
            )
        harness = self.get_harness(normalized)
        assert harness is not None
        return harness

    def get_harness(self, key: str) -> Harness | None:
        row = self._conn.execute("SELECT * FROM harnesses WHERE key = ?", (key.strip().lower(),)).fetchone()
        return self._harness_from_row(row) if row else None

    def list_harnesses(self) -> list[Harness]:
        rows = self._conn.execute("SELECT * FROM harnesses ORDER BY key").fetchall()
        return [self._harness_from_row(row) for row in rows]

    def harvestable_harnesses(self) -> list[Harness]:
        return [item for item in self.list_harnesses() if item.harvest_ok and item.session_root]

    @staticmethod
    def _harness_from_row(row: sqlite3.Row) -> Harness:
        return Harness(
            harness_id=int(row["harness_id"]),
            key=str(row["key"]),
            display_name=str(row["display_name"]),
            session_root=str(row["session_root"]),
            mcp_mounted=bool(row["mcp_mounted"]),
            harvest_ok=bool(row["harvest_ok"]),
            notes=str(row["notes"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    # Folders, sources, and incremental file state -------------------------
    def upsert_folder(self, path: str | Path, *, label: str = "") -> str:
        abs_path = str(require_absolute(path, name="folder"))
        with self.write_transaction():
            self._conn.execute(
                """
                INSERT INTO folders(path, label, enabled, created_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(path) DO UPDATE SET label = excluded.label, enabled = 1
                """,
                (abs_path, label, utc_now()),
            )
        return abs_path

    def list_folders(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT path, label, enabled, created_at FROM folders WHERE enabled = 1 ORDER BY path"
        ).fetchall()
        return [dict(row) for row in rows]

    def approve_source(
        self,
        canonical_root: str | Path,
        *,
        harness_key: str = "",
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        file_types: list[str] | None = None,
        max_file_bytes: int = 50 * 1024 * 1024,
        source_kind: str = "folder",
        actor: str = "local",
    ) -> dict[str, Any]:
        root = str(require_absolute(canonical_root, name="source root"))
        if max_file_bytes < 1:
            raise ValidationError("max_file_bytes must be positive")
        op = str(uuid.uuid4())
        with self.write_transaction():
            existing = self._conn.execute(
                "SELECT source_uid FROM sources WHERE canonical_root = ? AND source_kind = ?",
                (root, source_kind),
            ).fetchone()
            uid = str(existing["source_uid"]) if existing else str(uuid.uuid4())
            consent_uid = str(uuid.uuid4())
            now = utc_now()
            self._conn.execute(
                """
                INSERT INTO sources(
                    source_uid, source_kind, canonical_root, include_json, exclude_json,
                    file_types_json, max_file_bytes, harness_key, status, approved_at,
                    revoked_at, consent_uid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?, NULL, ?)
                ON CONFLICT(source_uid) DO UPDATE SET
                    include_json = excluded.include_json,
                    exclude_json = excluded.exclude_json,
                    file_types_json = excluded.file_types_json,
                    max_file_bytes = excluded.max_file_bytes,
                    harness_key = excluded.harness_key,
                    status = 'approved', approved_at = excluded.approved_at,
                    revoked_at = NULL, consent_uid = excluded.consent_uid
                """,
                (
                    uid,
                    source_kind,
                    root,
                    json.dumps(include or [], ensure_ascii=False),
                    json.dumps(exclude or [], ensure_ascii=False),
                    json.dumps(file_types or [], ensure_ascii=False),
                    int(max_file_bytes),
                    harness_key.strip().lower(),
                    now,
                    consent_uid,
                ),
            )
            self._audit(
                "source_approve",
                actor=actor,
                operation_uid=op,
                object_type="source",
                object_uid=uid,
                metadata={"source_kind": source_kind, "harness_key": harness_key},
            )
            self._change(
                "approved",
                object_type="source",
                object_uid=uid,
                operation_uid=op,
            )
        return self.get_source(uid)

    def ensure_import_source(self, source_path: str | Path, *, actor: str = "local") -> dict[str, Any]:
        return self.approve_source(
            source_path,
            source_kind="import",
            max_file_bytes=max(1, Path(source_path).stat().st_size),
            actor=actor,
        )

    def get_source(self, source_uid: str) -> dict[str, Any]:
        return self._source_to_dict(self._source_row(source_uid))

    def source_by_root(self, root: str | Path) -> dict[str, Any] | None:
        value = str(require_absolute(root, name="source root").resolve(strict=False))
        row = self._conn.execute(
            "SELECT * FROM sources WHERE canonical_root = ? ORDER BY source_id DESC LIMIT 1",
            (value,),
        ).fetchone()
        return self._source_to_dict(row) if row else None

    def list_sources(self, *, include_revoked: bool = False) -> list[dict[str, Any]]:
        sql = (
            "SELECT * FROM sources ORDER BY source_id"
            if include_revoked
            else "SELECT * FROM sources WHERE status = 'approved' ORDER BY source_id"
        )
        rows = self._conn.execute(sql).fetchall()
        return [self._source_to_dict(row) for row in rows]

    @staticmethod
    def _source_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "source_uid": str(row["source_uid"]),
            "source_kind": str(row["source_kind"]),
            "canonical_root": str(row["canonical_root"]),
            "include": json.loads(str(row["include_json"])),
            "exclude": json.loads(str(row["exclude_json"])),
            "file_types": json.loads(str(row["file_types_json"])),
            "max_file_bytes": int(row["max_file_bytes"]),
            "harness_key": str(row["harness_key"]),
            "status": str(row["status"]),
            "approved_at": str(row["approved_at"]),
            "revoked_at": row["revoked_at"],
            "consent_uid": str(row["consent_uid"]),
            "last_cursor": str(row["last_cursor"]),
        }

    def revoke_source(self, source_uid: str, *, actor: str = "local") -> dict[str, Any]:
        op = str(uuid.uuid4())
        with self.write_transaction():
            source = self._source_row(source_uid)
            if str(source["status"]) == "revoked":
                return self._source_to_dict(source)
            now = utc_now()
            source_id = int(source["source_id"])
            self._conn.execute(
                "UPDATE sources SET status = 'revoked', revoked_at = ?, last_cursor = '' WHERE source_id = ?",
                (now, source_id),
            )
            self._conn.execute(
                "UPDATE scan_runs SET status = 'revoked', completed_at = ? WHERE source_id = ? AND status = 'open'",
                (now, source_id),
            )
            self._conn.execute("DELETE FROM source_files WHERE source_id = ?", (source_id,))
            self._conn.execute("DELETE FROM files WHERE source_uid = ?", (source_uid,))
            self._conn.execute(
                """
                UPDATE candidates SET status = 'rejected', content = NULL,
                    content_summary = 'Source revoked; original candidate content removed.',
                    reason = 'source_revoked', updated_at = ?
                WHERE source_id = ? AND status = 'pending'
                """,
                (now, source_id),
            )
            self._conn.execute(
                "UPDATE folders SET enabled = 0 WHERE path = ?",
                (str(source["canonical_root"]),),
            )
            self._audit(
                "source_revoke",
                actor=actor,
                operation_uid=op,
                object_type="source",
                object_uid=source_uid,
            )
            self._change(
                "revoked",
                object_type="source",
                object_uid=source_uid,
                operation_uid=op,
            )
        return self.get_source(source_uid)

    def source_snapshot(self, source_uid: str) -> dict[str, dict[str, Any]]:
        source = self._source_row(source_uid)
        rows = self._conn.execute(
            """
            SELECT sf.* FROM source_files sf
            JOIN scan_runs sr ON sr.run_uid = sf.last_seen_run
            WHERE sf.source_id = ? AND sf.status = 'current'
                AND sr.status = 'complete'
            """,
            (int(source["source_id"]),),
        ).fetchall()
        # An observed file is not an acknowledged file. Incomplete and failed
        # runs must yield their changes again, even if the process restarted.
        return {str(row["path"]): dict(row) for row in rows}

    def start_scan(self, source_uid: str, principal_key: str) -> str:
        source = self._source_row(source_uid)
        if str(source["status"]) != "approved":
            raise ConflictError("source is revoked")
        principal = self._principal(principal_key)
        if principal is None:
            raise PermissionRequiredError(f"principal {principal_key!r} is not authorized")
        run_uid = str(uuid.uuid4())
        with self.write_transaction():
            self._conn.execute(
                """
                INSERT INTO scan_runs(run_uid, source_id, principal_id, status, started_at, cursor_before)
                VALUES (?, ?, ?, 'open', ?, ?)
                """,
                (
                    run_uid,
                    int(source["source_id"]),
                    int(principal["principal_id"]),
                    utc_now(),
                    str(source["last_cursor"]),
                ),
            )
        return run_uid

    def apply_scan(
        self,
        run_uid: str,
        *,
        changed: list[dict[str, Any]],
        unchanged_paths: list[str],
        seen_paths: set[str],
        truncated: bool,
        error_code: str = "",
    ) -> dict[str, int | bool]:
        with self.write_transaction():
            run = self._conn.execute("SELECT * FROM scan_runs WHERE run_uid = ?", (run_uid,)).fetchone()
            if run is None or str(run["status"]) != "open":
                raise ConflictError("scan run is not open")
            source_id = int(run["source_id"])
            source = self._conn.execute(
                "SELECT source_uid, canonical_root FROM sources WHERE source_id = ?", (source_id,)
            ).fetchone()
            assert source is not None
            now = utc_now()
            for record in changed:
                existing = self._conn.execute(
                    "SELECT file_uid FROM source_files WHERE source_id = ? AND path = ?",
                    (source_id, record["path"]),
                ).fetchone()
                file_uid = str(existing["file_uid"]) if existing else str(uuid.uuid4())
                self._conn.execute(
                    """
                    INSERT INTO source_files(
                        file_uid, source_id, path, name, sha256, size, mtime_ns,
                        status, last_seen_run, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'current', ?, ?)
                    ON CONFLICT(source_id, path) DO UPDATE SET
                        name = excluded.name, sha256 = excluded.sha256,
                        size = excluded.size, mtime_ns = excluded.mtime_ns,
                        status = 'current', last_seen_run = excluded.last_seen_run,
                        indexed_at = excluded.indexed_at
                    """,
                    (
                        file_uid,
                        source_id,
                        record["path"],
                        record["name"],
                        record["sha256"],
                        int(record["size"]),
                        int(record["mtime_ns"]),
                        run_uid,
                        now,
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO files(path, folder, name, sha256, size, mtime, indexed_at, status, source_uid)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'current', ?)
                    ON CONFLICT(path) DO UPDATE SET
                        folder = excluded.folder, name = excluded.name, sha256 = excluded.sha256,
                        size = excluded.size, mtime = excluded.mtime, indexed_at = excluded.indexed_at,
                        status = 'current', source_uid = excluded.source_uid
                    """,
                    (
                        record["path"],
                        str(source["canonical_root"]),
                        record["name"],
                        record["sha256"],
                        int(record["size"]),
                        float(record["mtime_ns"]) / 1_000_000_000,
                        now,
                        str(source["source_uid"]),
                    ),
                )
            if unchanged_paths:
                for batch in _chunks(unchanged_paths):
                    placeholders = ",".join("?" for _ in batch)
                    self._conn.execute(
                        # Only generated '?' placeholders are interpolated; paths remain bound.
                        # Keep the run that acknowledged this content version.
                        f"UPDATE source_files SET status = 'current' "  # noqa: S608
                        f"WHERE source_id = ? AND path IN ({placeholders})",
                        (source_id, *batch),
                    )
            existing_paths = {
                str(row["path"])
                for row in self._conn.execute(
                    "SELECT path FROM source_files WHERE source_id = ? AND status = 'current'",
                    (source_id,),
                ).fetchall()
            }
            # A truncated walk is not a complete snapshot; unseen rows must not
            # be declared stale until a complete run succeeds.
            stale_paths = set() if truncated else (existing_paths - seen_paths)
            if stale_paths:
                for batch in _chunks(stale_paths):
                    placeholders = ",".join("?" for _ in batch)
                    self._conn.execute(
                        f"UPDATE source_files SET status = 'stale' "  # noqa: S608 - placeholders only
                        f"WHERE source_id = ? AND path IN ({placeholders})",
                        (source_id, *batch),
                    )
                    self._conn.execute(
                        f"UPDATE files SET status = 'stale' "  # noqa: S608 - placeholders only
                        f"WHERE source_uid = ? AND path IN ({placeholders})",
                        (str(source["source_uid"]), *batch),
                    )
            self._conn.execute(
                """
                UPDATE scan_runs SET changed_count = ?, unchanged_count = ?, stale_count = ?,
                    truncated = ?, error_code = ?,
                    status = CASE WHEN ? THEN 'error' ELSE status END,
                    completed_at = CASE WHEN ? THEN ? ELSE completed_at END
                WHERE run_uid = ?
                """,
                (
                    len(changed),
                    len(unchanged_paths),
                    len(stale_paths),
                    int(truncated),
                    error_code,
                    int(truncated),
                    int(truncated),
                    now,
                    run_uid,
                ),
            )
        return {
            "changed": len(changed),
            "unchanged": len(unchanged_paths),
            "stale": len(stale_paths),
            "truncated": truncated,
        }

    def mark_source_missing(self, run_uid: str) -> int:
        with self.write_transaction():
            run = self._conn.execute("SELECT * FROM scan_runs WHERE run_uid = ?", (run_uid,)).fetchone()
            if run is None:
                raise NotFoundError(f"run_uid {run_uid} not found")
            source_id = int(run["source_id"])
            source = self._conn.execute("SELECT source_uid FROM sources WHERE source_id = ?", (source_id,)).fetchone()
            count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM source_files WHERE source_id = ? AND status = 'current'",
                    (source_id,),
                ).fetchone()[0]
            )
            self._conn.execute("UPDATE source_files SET status = 'stale' WHERE source_id = ?", (source_id,))
            if source:
                self._conn.execute(
                    "UPDATE files SET status = 'stale' WHERE source_uid = ?",
                    (str(source["source_uid"]),),
                )
            self._conn.execute(
                """
                UPDATE scan_runs SET status = 'error', completed_at = ?, stale_count = ?,
                    error_code = 'SOURCE_MISSING' WHERE run_uid = ?
                """,
                (utc_now(), count, run_uid),
            )
        return count

    def complete_scan(self, run_uid: str, *, cursor: str) -> dict[str, Any]:
        with self.write_transaction():
            run = self._conn.execute("SELECT * FROM scan_runs WHERE run_uid = ?", (run_uid,)).fetchone()
            if run is None:
                raise NotFoundError(f"run_uid {run_uid} not found")
            if str(run["status"]) != "open":
                raise ConflictError(f"scan run is {run['status']}")
            if bool(run["truncated"]) or str(run["error_code"]):
                raise ConflictError("cannot commit a truncated or failed scan cursor")
            now = utc_now()
            self._conn.execute(
                "UPDATE scan_runs SET status = 'complete', completed_at = ?, cursor_after = ? WHERE run_uid = ?",
                (now, cursor, run_uid),
            )
            self._conn.execute(
                "UPDATE sources SET last_cursor = ? WHERE source_id = ?",
                (cursor, int(run["source_id"])),
            )
        return self.scan_run(run_uid)

    def scan_run(self, run_uid: str) -> dict[str, Any]:
        row = self._conn.execute(
            """
            SELECT sr.*, s.source_uid FROM scan_runs sr
            JOIN sources s ON s.source_id = sr.source_id WHERE sr.run_uid = ?
            """,
            (run_uid,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"run_uid {run_uid} not found")
        return dict(row)

    def replace_files(self, folder: str, records: list[dict[str, Any]]) -> int:
        abs_folder = str(require_absolute(folder, name="folder"))
        seen = {str(record["path"]) for record in records}
        with self.write_transaction():
            existing = {
                str(row["path"])
                for row in self._conn.execute(
                    "SELECT path FROM files WHERE folder = ? AND status = 'current'", (abs_folder,)
                ).fetchall()
            }
            for record in records:
                mtime = float(record.get("mtime", float(record.get("mtime_ns", 0)) / 1_000_000_000))
                self._conn.execute(
                    """
                    INSERT INTO files(path, folder, name, sha256, size, mtime, indexed_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'current')
                    ON CONFLICT(path) DO UPDATE SET
                        folder = excluded.folder, name = excluded.name, sha256 = excluded.sha256,
                        size = excluded.size, mtime = excluded.mtime, indexed_at = excluded.indexed_at,
                        status = 'current'
                    """,
                    (
                        record["path"],
                        abs_folder,
                        record["name"],
                        record["sha256"],
                        int(record["size"]),
                        mtime,
                        utc_now(),
                    ),
                )
            stale = existing - seen
            if stale:
                for batch in _chunks(stale):
                    placeholders = ",".join("?" for _ in batch)
                    self._conn.execute(
                        f"UPDATE files SET status = 'stale' "  # noqa: S608 - placeholders only
                        f"WHERE folder = ? AND path IN ({placeholders})",
                        (abs_folder, *batch),
                    )
        return len(records)

    def mark_folder_stale(self, folder: str) -> int:
        abs_folder = str(require_absolute(folder, name="folder"))
        with self.write_transaction():
            cur = self._conn.execute(
                "UPDATE files SET status = 'stale' WHERE folder = ? AND status = 'current'",
                (abs_folder,),
            )
        return int(cur.rowcount)

    @staticmethod
    def _like_pattern(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    def search_files(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if not 1 <= int(limit) <= 100:
            raise ValidationError("limit must be between 1 and 100")
        tokens = [token for token in query.split() if token]
        if not tokens:
            return []
        where = " AND ".join(["(path LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\')"] * len(tokens))
        params: list[Any] = []
        for token in tokens:
            pattern = self._like_pattern(token)
            params.extend([pattern, pattern])
        rows = self._conn.execute(
            # `where` repeats one fixed LIKE predicate; every search token remains bound.
            f"SELECT * FROM files WHERE status = 'current' AND {where} "  # noqa: S608
            "ORDER BY mtime DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_files(self, limit: int = 200, *, include_stale: bool = False) -> list[dict[str, Any]]:
        if not 1 <= int(limit) <= 100000:
            raise ValidationError("file limit must be between 1 and 100000")
        sql = (
            "SELECT * FROM files ORDER BY mtime DESC LIMIT ?"
            if include_stale
            else "SELECT * FROM files WHERE status = 'current' ORDER BY mtime DESC LIMIT ?"
        )
        rows = self._conn.execute(sql, (limit,)).fetchall()
        return [dict(row) for row in rows]

    # Candidate lifecycle --------------------------------------------------
    def _candidate_from_row(self, row: sqlite3.Row) -> Candidate:
        return Candidate(
            candidate_id=int(row["candidate_id"]),
            candidate_uid=str(row["candidate_uid"]),
            submission_uid=str(row["submission_uid"]),
            principal_key=str(row["principal_key"]),
            content=row["content"],
            content_summary=str(row["content_summary"]),
            category=str(row["category"]),
            entities=list(json.loads(str(row["entities_json"]))),
            as_of=str(row["as_of"]),
            confidence=float(row["confidence"]),
            status=str(row["status"]),
            scope={"kind": str(row["scope_kind"]), "key": str(row["scope_key"])},
            source_uid=str(row["source_uid"]) if row["source_uid"] is not None else None,
            source_locator=str(row["source_locator"]),
            manual_input=bool(row["manual_input"]),
            policy_codes=list(json.loads(str(row["policy_codes_json"]))),
            reason=str(row["reason"]),
            accepted_fact_id=row["accepted_fact_id"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            expires_at=str(row["expires_at"]),
        )

    def _candidate_query(self, where: str, params: tuple[Any, ...]) -> list[Candidate]:
        rows = self._conn.execute(
            f"SELECT c.*, p.key AS principal_key, s.scope_kind, s.scope_key, "  # noqa: S608
            "src.source_uid AS source_uid FROM candidates c "
            "JOIN principals p ON p.principal_id = c.principal_id "
            "JOIN scopes s ON s.scope_id = c.scope_id "
            "LEFT JOIN sources src ON src.source_id = c.source_id "
            f"WHERE {where}",
            params,
        ).fetchall()
        return [self._candidate_from_row(row) for row in rows]

    def expire_candidates(self) -> int:
        op = str(uuid.uuid4())
        with self.write_transaction():
            now = utc_now()
            rows = self._conn.execute(
                "SELECT candidate_uid FROM candidates WHERE status = 'pending' AND expires_at <= ?",
                (now,),
            ).fetchall()
            self._conn.execute(
                """
                UPDATE candidates SET status = 'expired', content = NULL,
                    content_summary = 'Candidate expired; original content removed.',
                    reason = 'retention_expired', updated_at = ?
                WHERE status = 'pending' AND expires_at <= ?
                """,
                (now, now),
            )
            for row in rows:
                self._change(
                    "expired",
                    object_type="candidate",
                    object_uid=str(row["candidate_uid"]),
                    operation_uid=op,
                )
        return len(rows)

    def expired_candidate_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM candidates WHERE status = 'pending' AND expires_at <= ?",
            (utc_now(),),
        ).fetchone()
        return int(row[0])

    def add_candidate(
        self,
        *,
        submission_uid: str,
        principal_key: str,
        content: str | None,
        content_summary: str,
        category: str,
        entities: list[str],
        as_of: str,
        confidence: float,
        scope: ScopeSpec,
        source_uid: str | None,
        source_locator: str,
        manual_input: bool,
        status: str = "pending",
        policy_codes: list[str] | None = None,
        actor: str | None = None,
        operation_uid: str | None = None,
    ) -> tuple[Candidate, bool]:
        if status not in {"pending", "quarantined"}:
            raise ValidationError("new candidates must be pending or quarantined")
        if not submission_uid.strip() or len(submission_uid) > 200:
            raise ValidationError("submission_uid is required and must be at most 200 characters")
        op = operation_uid or str(uuid.uuid4())
        with self.write_transaction():
            existing = self._candidate_query(
                "c.submission_uid = ? AND p.key = ?",
                (submission_uid, principal_key.strip().lower()),
            )
            if existing:
                return existing[0], False
            principal = self._principal(principal_key)
            if principal is None or not bool(principal["enabled"]):
                raise PermissionRequiredError(f"principal {principal_key!r} is not authorized")
            scope_id, _ = self._ensure_scope(scope)
            source_id: int | None = None
            if source_uid:
                source = self._source_row(source_uid)
                if str(source["status"]) != "approved":
                    raise ConflictError("candidate source is revoked")
                source_id = int(source["source_id"])
            candidate_uid = str(uuid.uuid4())
            now_dt = datetime.now(timezone.utc)
            now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            expires = (now_dt + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._conn.execute(
                """
                INSERT INTO candidates(
                    candidate_uid, submission_uid, principal_id, scope_id, source_id,
                    source_locator, manual_input, content, content_summary, category,
                    entities_json, as_of, confidence, status, policy_codes_json,
                    created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_uid,
                    submission_uid,
                    int(principal["principal_id"]),
                    scope_id,
                    source_id,
                    source_locator,
                    int(manual_input),
                    content,
                    content_summary,
                    category or "general",
                    json.dumps(normalize_names(entities), ensure_ascii=False),
                    as_of,
                    clamp_trust(confidence),
                    status,
                    json.dumps(policy_codes or [], ensure_ascii=False),
                    now,
                    now,
                    expires,
                ),
            )
            self._audit(
                "candidate_submit",
                actor=actor or principal_key,
                operation_uid=op,
                object_type="candidate",
                object_uid=candidate_uid,
                metadata={"status": status, "scope": scope.fingerprint},
            )
            self._change(
                "submitted",
                object_type="candidate",
                object_uid=candidate_uid,
                operation_uid=op,
                metadata={"status": status},
            )
            created = self._candidate_query("c.candidate_uid = ?", (candidate_uid,))[0]
        return created, True

    def get_candidate(self, candidate_uid: str) -> Candidate:
        rows = self._candidate_query("c.candidate_uid = ?", (candidate_uid,))
        if not rows:
            raise NotFoundError(f"candidate_uid {candidate_uid} not found")
        return rows[0]

    def list_candidates(
        self,
        *,
        status: str | None = "pending",
        principal_key: str | None = None,
        limit: int = 100,
    ) -> list[Candidate]:
        if not 1 <= int(limit) <= 500:
            raise ValidationError("candidate limit must be between 1 and 500")
        clauses = ["1 = 1"]
        params: list[Any] = []
        if status:
            clauses.append("c.status = ?")
            params.append(status)
        if principal_key:
            clauses.append("p.key = ?")
            params.append(principal_key)
        return self._candidate_query(
            " AND ".join(clauses) + " ORDER BY c.created_at, c.candidate_id LIMIT ?",
            (*params, limit),
        )

    def accept_candidate(
        self,
        candidate_uid: str,
        *,
        supersedes: list[int] | None = None,
        actor: str = "local",
        operation_uid: str | None = None,
    ) -> tuple[Candidate, Fact, list[Fact]]:
        with self.maintenance_lock():
            if self.expired_candidate_count():
                self.expire_candidates()
            try:
                return self._accept_current_candidate(
                    candidate_uid, supersedes=supersedes, actor=actor, operation_uid=operation_uid
                )
            except ConflictError:
                # A candidate can expire after the cleanup check. Persist its
                # expiry after the failed acceptance transaction has rolled back.
                if self.expired_candidate_count():
                    self.expire_candidates()
                raise

    def _accept_current_candidate(
        self,
        candidate_uid: str,
        *,
        supersedes: list[int] | None = None,
        actor: str = "local",
        operation_uid: str | None = None,
    ) -> tuple[Candidate, Fact, list[Fact]]:
        op = operation_uid or str(uuid.uuid4())
        old_ids = list(dict.fromkeys(supersedes or []))
        with self.write_transaction():
            candidate = self.get_candidate(candidate_uid)
            if candidate.status != "pending" or candidate.content is None:
                raise ConflictError(f"candidate is {candidate.status}, not pending")
            if candidate.expires_at <= utc_now():
                raise ConflictError("candidate has expired")
            if candidate.source_uid and self.get_source(candidate.source_uid)["status"] != "approved":
                raise ConflictError("candidate source is revoked")
            if len(old_ids) > 50:
                raise ValidationError("at most 50 facts may be superseded in one review action")
            fact, _ = self._insert_fact_no_tx(
                candidate.content,
                category=candidate.category,
                tags="",
                trust=candidate.confidence,
                as_of=candidate.as_of,
                entities=candidate.entities,
                source_kind="candidate",
                source_ref=candidate.candidate_uid,
                source_uid=candidate.source_uid,
                source_locator=candidate.source_locator,
                actor=actor,
                status="active",
                created_at=utc_now(),
                scope=validate_scope(candidate.scope["kind"], candidate.scope["key"]),
                operation_uid=op,
            )
            deprecated: list[Fact] = []
            for old_id in old_ids:
                if old_id != fact.fact_id:
                    deprecated.append(
                        self._deprecate_no_tx(
                            old_id,
                            superseded_by=fact.fact_id,
                            reason="accepted candidate supersession",
                            actor=actor,
                            operation_uid=op,
                        )
                    )
            self._conn.execute(
                """
                UPDATE candidates SET status = 'accepted', content = NULL,
                    content_summary = 'Accepted as fact; original is stored only on the fact.',
                    accepted_fact_id = ?, updated_at = ? WHERE candidate_uid = ?
                """,
                (fact.fact_id, utc_now(), candidate_uid),
            )
            self._audit(
                "candidate_accept",
                actor=actor,
                operation_uid=op,
                object_type="candidate",
                object_uid=candidate_uid,
                metadata={"accepted_fact_id": fact.fact_id, "superseded_count": len(deprecated)},
            )
            self._change(
                "accepted",
                object_type="candidate",
                object_uid=candidate_uid,
                operation_uid=op,
                metadata={"accepted_fact_id": fact.fact_id},
            )
            updated = self.get_candidate(candidate_uid)
        return updated, fact, deprecated

    def reject_candidate(
        self,
        candidate_uid: str,
        *,
        reason: str,
        actor: str = "local",
        operation_uid: str | None = None,
    ) -> Candidate:
        op = operation_uid or str(uuid.uuid4())
        with self.write_transaction():
            candidate = self.get_candidate(candidate_uid)
            if candidate.status != "pending":
                raise ConflictError(f"candidate is {candidate.status}, not pending")
            self._conn.execute(
                """
                UPDATE candidates SET status = 'rejected', content = NULL,
                    content_summary = 'Candidate rejected; original content removed.',
                    reason = ?, updated_at = ? WHERE candidate_uid = ?
                """,
                (reason[:300], utc_now(), candidate_uid),
            )
            self._audit(
                "candidate_reject",
                actor=actor,
                operation_uid=op,
                object_type="candidate",
                object_uid=candidate_uid,
                metadata={"reason_code": "provided" if reason else "none"},
            )
            self._change(
                "rejected",
                object_type="candidate",
                object_uid=candidate_uid,
                operation_uid=op,
            )
            updated = self.get_candidate(candidate_uid)
        return updated

    # Health, projection, and controlled purge ----------------------------
    def set_projection_state(self, status: str, *, operation_uid: str = "", error_code: str = "") -> None:
        if status not in {"clean", "dirty", "disabled"}:
            raise ValidationError("invalid projection status")
        with self.write_transaction():
            self._conn.execute(
                """
                INSERT INTO projection_state(projection_key, status, operation_uid, error_code, updated_at)
                VALUES ('obsidian', ?, ?, ?, ?)
                ON CONFLICT(projection_key) DO UPDATE SET
                    status = excluded.status, operation_uid = excluded.operation_uid,
                    error_code = excluded.error_code, updated_at = excluded.updated_at
                """,
                (status, operation_uid, error_code, utc_now()),
            )

    def projection_state(self) -> dict[str, Any]:
        if self.schema_version < 2:
            return {"status": "unknown", "error_code": "SCHEMA_V1"}
        row = self._conn.execute("SELECT * FROM projection_state WHERE projection_key = 'obsidian'").fetchone()
        return dict(row) if row else {"status": "unknown"}

    def integrity(self) -> dict[str, Any]:
        integrity_rows = [str(row[0]) for row in self._conn.execute("PRAGMA integrity_check").fetchall()]
        foreign_keys = [dict(row) for row in self._conn.execute("PRAGMA foreign_key_check").fetchall()]
        result: dict[str, Any] = {
            "integrity": integrity_rows,
            "foreign_key_violations": foreign_keys,
            "ok": integrity_rows == ["ok"] and not foreign_keys,
        }
        if self.schema_version >= 2:
            fact_count = int(self._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
            fts_count = int(self._conn.execute("SELECT COUNT(*) FROM facts_fts").fetchone()[0])
            result.update({"fact_count": fact_count, "fts_count": fts_count})
            result["ok"] = bool(result["ok"] and fact_count == fts_count)
        return result

    def purge_fact_row(self, fact_uid: str, *, actor: str = "local") -> dict[str, Any]:
        self._require_write()
        fact = self.get_fact_by_uid(fact_uid)
        if fact is None:
            raise NotFoundError(f"fact_uid {fact_uid} not found")
        tombstone_uid = str(uuid.uuid4())
        self._conn.execute("PRAGMA secure_delete = ON")
        with self.write_transaction():
            self._conn.execute("UPDATE facts SET superseded_by = NULL WHERE superseded_by = ?", (fact.fact_id,))
            self._conn.execute(
                "DELETE FROM supersessions WHERE old_fact_id = ? OR new_fact_id = ?",
                (fact.fact_id, fact.fact_id),
            )
            self._conn.execute(
                """
                UPDATE candidates SET accepted_fact_id = NULL, source_locator = ''
                WHERE accepted_fact_id = ?
                """,
                (fact.fact_id,),
            )
            self._conn.execute("DELETE FROM audit WHERE fact_id = ?", (fact.fact_id,))
            self._conn.execute(
                "DELETE FROM audit_events WHERE object_type = 'fact' AND object_uid = ?",
                (fact_uid,),
            )
            self._conn.execute(
                "DELETE FROM change_events WHERE object_type = 'fact' AND object_uid = ?",
                (fact_uid,),
            )
            self._conn.execute("DELETE FROM facts WHERE fact_id = ?", (fact.fact_id,))
            self._conn.execute(
                "INSERT INTO purge_tombstones(tombstone_uid, fact_uid, purged_at) VALUES (?, ?, ?)",
                (tombstone_uid, fact_uid, utc_now()),
            )
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._conn.execute("VACUUM")
        health = self.integrity()
        if not health["ok"]:
            raise IntegrityCheckError("database failed integrity checks after purge")
        return {
            "fact_id": fact.fact_id,
            "fact_uid": fact_uid,
            "tombstone_uid": tombstone_uid,
            "integrity": health,
        }

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn
