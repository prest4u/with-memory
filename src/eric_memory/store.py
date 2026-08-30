"""Owned SQLite fact store. Writes go through this module only."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .entities import collect_entities, is_pure_name, normalize_names
from .models import AuditEvent, Fact, Harness
from .paths import PathError, require_absolute

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    fact_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    content       TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT 'general',
    tags          TEXT NOT NULL DEFAULT '',
    trust         REAL NOT NULL DEFAULT 0.5,
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'deprecated')),
    as_of         TEXT NOT NULL,
    superseded_by INTEGER REFERENCES facts(fact_id),
    source_kind   TEXT NOT NULL DEFAULT 'manual',
    source_ref    TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_active_content
    ON facts(content) WHERE status = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_source
    ON facts(source_kind, source_ref) WHERE source_ref != '';
CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status);
CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
CREATE INDEX IF NOT EXISTS idx_facts_as_of ON facts(as_of);

CREATE TABLE IF NOT EXISTS entities (
    entity_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    name_key    TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL DEFAULT 'unknown',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

CREATE TABLE IF NOT EXISTS fact_entities (
    fact_id   INTEGER NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    PRIMARY KEY (fact_id, entity_id)
);

CREATE TABLE IF NOT EXISTS folders (
    folder_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT NOT NULL UNIQUE,
    label      TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    file_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT NOT NULL UNIQUE,
    folder     TEXT NOT NULL,
    name       TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    size       INTEGER NOT NULL,
    mtime      REAL NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder);
CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);

CREATE TABLE IF NOT EXISTS harnesses (
    harness_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    session_root TEXT NOT NULL DEFAULT '',
    mcp_mounted  INTEGER NOT NULL DEFAULT 0,
    harvest_ok   INTEGER NOT NULL DEFAULT 0,
    notes        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    audit_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    action     TEXT NOT NULL,
    fact_id    INTEGER,
    actor      TEXT NOT NULL DEFAULT 'cli',
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
    USING fts5(content, tags, content=facts, content_rowid=fact_id, tokenize='unicode61');

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content, tags)
        VALUES (new.fact_id, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags)
        VALUES ('delete', old.fact_id, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags)
        VALUES ('delete', old.fact_id, old.content, old.tags);
    INSERT INTO facts_fts(rowid, content, tags)
        VALUES (new.fact_id, new.content, new.tags);
END;
"""

SCHEMA_VERSION = "1"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def clamp_trust(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class MemoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = require_absolute(db_path, name="db path")
        if self.db_path.suffix == "" and str(self.db_path).endswith("~"):
            raise PathError("db path must not contain '~'")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            self._conn.commit()

    def _audit(self, action: str, *, fact_id: int | None = None, actor: str = "cli", detail: str = "") -> None:
        self._conn.execute(
            "INSERT INTO audit(action, fact_id, actor, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (action, fact_id, actor, detail, utc_now()),
        )

    def _known_entity_names(self) -> list[str]:
        rows = self._conn.execute("SELECT name FROM entities ORDER BY entity_id").fetchall()
        return [r["name"] for r in rows]

    def _resolve_entity(self, name: str, entity_type: str = "unknown") -> int | None:
        if not is_pure_name(name):
            return None
        key = name.casefold()
        row = self._conn.execute("SELECT entity_id FROM entities WHERE name_key = ?", (key,)).fetchone()
        if row:
            return int(row["entity_id"])
        cur = self._conn.execute(
            "INSERT INTO entities(name, name_key, entity_type, created_at) VALUES (?, ?, ?, ?)",
            (name, key, entity_type, utc_now()),
        )
        return int(cur.lastrowid)

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

    def _entities_for(self, fact_id: int) -> list[str]:
        rows = self._conn.execute(
            """
            SELECT e.name FROM entities e
            JOIN fact_entities fe ON fe.entity_id = e.entity_id
            WHERE fe.fact_id = ?
            ORDER BY e.name
            """,
            (fact_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    def _row_to_fact(self, row: sqlite3.Row) -> Fact:
        return Fact(
            fact_id=int(row["fact_id"]),
            content=row["content"],
            category=row["category"],
            tags=row["tags"],
            trust=float(row["trust"]),
            status=row["status"],
            as_of=row["as_of"],
            superseded_by=row["superseded_by"],
            source_kind=row["source_kind"],
            source_ref=row["source_ref"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            entities=self._entities_for(int(row["fact_id"])),
        )

    def get_fact(self, fact_id: int) -> Fact | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM facts WHERE fact_id = ?", (fact_id,)).fetchone()
            return self._row_to_fact(row) if row else None

    def add_fact(
        self,
        content: str,
        *,
        category: str = "general",
        tags: str = "",
        trust: float = 0.5,
        as_of: str | None = None,
        entities: list[str] | None = None,
        source_kind: str = "manual",
        source_ref: str = "",
        actor: str = "cli",
        status: str = "active",
        created_at: str | None = None,
    ) -> Fact:
        content = content.strip()
        if not content:
            raise ValueError("content must not be empty")
        if status not in {"active", "deprecated"}:
            raise ValueError(f"invalid status: {status}")
        as_of_value = (as_of or today_utc()).strip()
        now = utc_now()
        created = created_at or now
        with self._lock:
            if source_ref:
                existing = self._conn.execute(
                    "SELECT * FROM facts WHERE source_kind = ? AND source_ref = ?",
                    (source_kind, source_ref),
                ).fetchone()
                if existing:
                    return self._row_to_fact(existing)
            if status == "active":
                dup = self._conn.execute(
                    "SELECT * FROM facts WHERE content = ? AND status = 'active'",
                    (content,),
                ).fetchone()
                if dup:
                    return self._row_to_fact(dup)
            cur = self._conn.execute(
                """
                INSERT INTO facts(
                    content, category, tags, trust, status, as_of,
                    source_kind, source_ref, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    content,
                    category or "general",
                    tags or "",
                    clamp_trust(trust),
                    status,
                    as_of_value,
                    source_kind,
                    source_ref or "",
                    created,
                    now,
                ),
            )
            fact_id = int(cur.lastrowid)
            known = self._known_entity_names()
            names = collect_entities(content, entities, known)
            self._link_entities(fact_id, names)
            self._audit("add", fact_id=fact_id, actor=actor, detail=source_kind)
            self._conn.commit()
            fact = self.get_fact(fact_id)
            assert fact is not None
            return fact

    def deprecate(
        self,
        fact_id: int,
        *,
        superseded_by: int | None = None,
        reason: str = "",
        actor: str = "cli",
    ) -> Fact:
        with self._lock:
            row = self._conn.execute("SELECT * FROM facts WHERE fact_id = ?", (fact_id,)).fetchone()
            if row is None:
                raise KeyError(f"fact_id {fact_id} not found")
            if superseded_by is not None:
                successor = self._conn.execute(
                    "SELECT fact_id FROM facts WHERE fact_id = ?",
                    (superseded_by,),
                ).fetchone()
                if successor is None:
                    raise KeyError(f"superseded_by {superseded_by} not found")
                if int(superseded_by) == int(fact_id):
                    raise ValueError("a fact cannot supersede itself")
            self._conn.execute(
                """
                UPDATE facts
                SET status = 'deprecated',
                    superseded_by = ?,
                    updated_at = ?
                WHERE fact_id = ?
                """,
                (superseded_by, utc_now(), fact_id),
            )
            detail = reason or (f"superseded_by={superseded_by}" if superseded_by else "deprecated")
            self._audit("deprecate", fact_id=fact_id, actor=actor, detail=detail)
            self._conn.commit()
            fact = self.get_fact(fact_id)
            assert fact is not None
            return fact

    def add_and_supersede(
        self,
        content: str,
        old_fact_id: int,
        **kwargs,
    ) -> tuple[Fact, Fact]:
        new_fact = self.add_fact(content, **kwargs)
        old = self.deprecate(
            old_fact_id,
            superseded_by=new_fact.fact_id,
            reason=kwargs.get("reason", ""),
            actor=kwargs.get("actor", "cli"),
        )
        return new_fact, old

    def counts(self) -> dict[str, int]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            active = self._conn.execute(
                "SELECT COUNT(*) FROM facts WHERE status = 'active'"
            ).fetchone()[0]
            deprecated = self._conn.execute(
                "SELECT COUNT(*) FROM facts WHERE status = 'deprecated'"
            ).fetchone()[0]
            entities = self._conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            files = self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            harnesses = self._conn.execute("SELECT COUNT(*) FROM harnesses").fetchone()[0]
            folders = self._conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0]
        return {
            "facts": int(total),
            "active": int(active),
            "deprecated": int(deprecated),
            "entities": int(entities),
            "files": int(files),
            "harnesses": int(harnesses),
            "folders": int(folders),
        }

    def recent_audit(self, limit: int = 20) -> list[AuditEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit ORDER BY audit_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            AuditEvent(
                audit_id=int(r["audit_id"]),
                action=r["action"],
                fact_id=r["fact_id"],
                actor=r["actor"],
                detail=r["detail"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def upsert_harness(
        self,
        key: str,
        *,
        display_name: str,
        session_root: str = "",
        mcp_mounted: bool = False,
        harvest_ok: bool = False,
        notes: str = "",
        actor: str = "cli",
    ) -> Harness:
        key = key.strip().lower()
        if not key:
            raise ValueError("harness key is required")
        if session_root:
            session_root = str(require_absolute(session_root, name="session_root"))
        now = utc_now()
        with self._lock:
            existing = self._conn.execute("SELECT * FROM harnesses WHERE key = ?", (key,)).fetchone()
            if existing:
                self._conn.execute(
                    """
                    UPDATE harnesses
                    SET display_name = ?, session_root = ?, mcp_mounted = ?,
                        harvest_ok = ?, notes = ?, updated_at = ?
                    WHERE key = ?
                    """,
                    (
                        display_name,
                        session_root,
                        int(mcp_mounted),
                        int(harvest_ok),
                        notes,
                        now,
                        key,
                    ),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO harnesses(
                        key, display_name, session_root, mcp_mounted,
                        harvest_ok, notes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        display_name,
                        session_root,
                        int(mcp_mounted),
                        int(harvest_ok),
                        notes,
                        now,
                        now,
                    ),
                )
            self._audit("harness_add", actor=actor, detail=key)
            self._conn.commit()
            return self.get_harness(key)  # type: ignore[return-value]

    def get_harness(self, key: str) -> Harness | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM harnesses WHERE key = ?", (key.strip().lower(),)
            ).fetchone()
        return self._harness_from_row(row) if row else None

    def list_harnesses(self) -> list[Harness]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM harnesses ORDER BY key").fetchall()
        return [self._harness_from_row(r) for r in rows]

    def harvestable_harnesses(self) -> list[Harness]:
        return [h for h in self.list_harnesses() if h.harvest_ok and h.session_root]

    def upsert_folder(self, path: str, *, label: str = "") -> str:
        abs_path = str(require_absolute(path, name="folder"))
        now = utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO folders(path, label, enabled, created_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(path) DO UPDATE SET label = excluded.label, enabled = 1
                """,
                (abs_path, label, now),
            )
            self._conn.commit()
        return abs_path

    def list_folders(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT path, label, enabled, created_at FROM folders WHERE enabled = 1 ORDER BY path"
            ).fetchall()
        return [dict(r) for r in rows]

    def replace_files(self, folder: str, records: list[dict]) -> int:
        abs_folder = str(require_absolute(folder, name="folder"))
        now = utc_now()
        with self._lock:
            self._conn.execute("DELETE FROM files WHERE folder = ?", (abs_folder,))
            for rec in records:
                self._conn.execute(
                    """
                    INSERT INTO files(path, folder, name, sha256, size, mtime, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        folder = excluded.folder,
                        name = excluded.name,
                        sha256 = excluded.sha256,
                        size = excluded.size,
                        mtime = excluded.mtime,
                        indexed_at = excluded.indexed_at
                    """,
                    (
                        rec["path"],
                        abs_folder,
                        rec["name"],
                        rec["sha256"],
                        rec["size"],
                        rec["mtime"],
                        now,
                    ),
                )
            self._audit("index", detail=f"{abs_folder}:{len(records)}")
            self._conn.commit()
        return len(records)

    def search_files(self, query: str, limit: int = 20) -> list[dict]:
        tokens = [t for t in query.split() if t]
        if not tokens:
            return []
        where = " AND ".join(["(path LIKE ? OR name LIKE ?)"] * len(tokens))
        params: list = []
        for token in tokens:
            params.extend([f"%{token}%", f"%{token}%"])
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM files WHERE {where} ORDER BY mtime DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def list_files(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM files ORDER BY mtime DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _harness_from_row(row: sqlite3.Row) -> Harness:
        return Harness(
            harness_id=int(row["harness_id"]),
            key=row["key"],
            display_name=row["display_name"],
            session_root=row["session_root"],
            mcp_mounted=bool(row["mcp_mounted"]),
            harvest_ok=bool(row["harvest_ok"]),
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn
