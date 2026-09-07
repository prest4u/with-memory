"""Optional, disposable working documents. Durable facts never enter this database.

This is an independent implementation using the Python standard library. A source
consent revision is pinned at enable/index time and checked by ContextService on
every read. Reads do not create databases, refresh TTLs, or run cleanup.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from .errors import ConflictError, IntegrityCheckError, NotFoundError, ValidationError
from .locking import exclusive_file_lock
from .security import harden_private_path

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_PROJECT_BYTES = 50 * 1024 * 1024
MAX_PROJECT_DOCUMENTS = 1000
MAX_CHUNK_CHARS = 2400
MAX_CHUNKS = 4096

SCHEMA = """
CREATE TABLE IF NOT EXISTS context_meta(version INTEGER NOT NULL, library_uid TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS context_sources(
    project TEXT NOT NULL, source_uid TEXT NOT NULL, consent_uid TEXT NOT NULL,
    ttl_days INTEGER NOT NULL, PRIMARY KEY(project, source_uid)
);
CREATE TABLE IF NOT EXISTS artifacts(
    artifact_uid TEXT PRIMARY KEY, project TEXT NOT NULL, source_uid TEXT NOT NULL,
    consent_uid TEXT NOT NULL, source_locator TEXT NOT NULL, label TEXT NOT NULL,
    sha256 TEXT NOT NULL, content TEXT NOT NULL, byte_count INTEGER NOT NULL,
    line_count INTEGER NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
    UNIQUE(project, source_uid, source_locator)
);
CREATE INDEX IF NOT EXISTS artifacts_project ON artifacts(project, expires_at);
CREATE TABLE IF NOT EXISTS context_chunks(
    chunk_id INTEGER PRIMARY KEY, artifact_uid TEXT NOT NULL REFERENCES artifacts ON DELETE CASCADE,
    start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, content TEXT NOT NULL, terms TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_artifact ON context_chunks(artifact_uid);
CREATE VIRTUAL TABLE IF NOT EXISTS context_fts USING fts5(
    terms, content=context_chunks, content_rowid=chunk_id, tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON context_chunks BEGIN
    INSERT INTO context_fts(rowid, terms) VALUES(new.chunk_id, new.terms);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON context_chunks BEGIN
    INSERT INTO context_fts(context_fts, rowid, terms) VALUES('delete', old.chunk_id, old.terms);
END;
"""


def search_tokens(text: str) -> list[str]:
    """Latin identifiers/parts and CJK bigrams share one portable FTS index."""
    tokens: list[str] = []
    for original in re.findall(r"[\u3400-\u9fff]+|[A-Za-z0-9_]+", text):
        run = original.casefold()
        if "\u3400" <= run[0] <= "\u9fff":
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
            if len(run) == 1:
                tokens.append(run)
        else:
            tokens.append(run)
            tokens.extend(part for part in run.split("_") if part != run and part)
            tokens.extend(re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", original).casefold().split())
    return list(dict.fromkeys(tokens))


def chunk_text(content: str) -> list[tuple[int, int, str]]:
    """Keep ordinary fenced examples together; split oversized lines explicitly.

    Line coordinates always refer to the captured original, including when a
    single long line spans several chunks. context read retrieves the full range.
    """
    result: list[tuple[int, int, str]] = []
    pending: list[str] = []
    start = 1
    size = 0
    fence = False
    lines = content.splitlines(keepends=True)
    for number, line in enumerate(lines, 1):
        if pending and (size + len(line) > MAX_CHUNK_CHARS or (line.startswith("#") and not fence)):
            result.append((start, number - 1, "".join(pending)))
            pending, size = [], 0
        if not pending:
            start = number
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        if len(line) > MAX_CHUNK_CHARS:
            result.extend((number, number, line[i : i + MAX_CHUNK_CHARS]) for i in range(0, len(line), MAX_CHUNK_CHARS))
            continue
        pending.append(line)
        size += len(line)
    if pending:
        result.append((start, len(lines), "".join(pending)))
    if len(result) > MAX_CHUNKS:
        raise ValidationError("document has too many sections; split the input file")
    return result


def encoded_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def bounded_results(
    payload: dict[str, Any], entries: list[dict[str, Any]], max_bytes: int, *, sequential: bool = False
) -> dict[str, Any]:
    """Bound the compact UTF-8 JSON payload, including all result metadata."""
    if not 2048 <= max_bytes <= 32768:
        raise ValidationError("max_bytes must be between 2048 and 32768")
    payload.update(results=[], truncated=False, max_bytes=max_bytes, returned_bytes=0)
    for entry in entries:
        trial = {**payload, "results": [*payload["results"], entry], "returned_bytes": max_bytes}
        if encoded_size(trial) > max_bytes:
            payload["truncated"] = True
            content = entry.get("content", "")
            low, high = 0, len(content)
            while low < high:
                middle = (low + high + 1) // 2
                clipped = {**entry, "content": content[:middle], "content_truncated": True}
                trial["results"] = [*payload["results"], clipped]
                if encoded_size(trial) <= max_bytes:
                    low = middle
                else:
                    high = middle - 1
            if low:
                payload["results"].append({**entry, "content": content[:low], "content_truncated": True})
            if sequential:
                break
            continue
        payload["results"].append(entry)
    # A fixed point includes the digits of the size itself.
    for _ in range(5):
        payload["returned_bytes"] = encoded_size(payload)
    if encoded_size(payload) > max_bytes:
        raise ValidationError("response metadata exceeds max_bytes")
    return payload


class ContextStore:
    def __init__(self, data_dir: Path, library_uid: str) -> None:
        self.directory = data_dir / "context"
        self.path = self.directory / "working.sqlite3"
        self.library_uid = library_uid

    def _check_paths(self) -> None:
        for path in (
            self.directory,
            self.directory / "access.lock",
            self.path,
            *(Path(str(self.path) + suffix) for suffix in ("-journal", "-wal", "-shm")),
        ):
            if not path.exists() and not path.is_symlink():
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValidationError("context storage cannot use symbolic links or reparse points")
            if path == self.directory:
                if not stat.S_ISDIR(info.st_mode):
                    raise ValidationError("context storage must be a directory")
            elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValidationError("context storage files must be regular files with one hard link")

    @contextmanager
    def _access(self) -> Iterator[None]:
        """Coordinate open connections and cache removal across processes."""
        self._check_paths()
        deadline = time.monotonic() + 30
        with ExitStack() as stack:
            while True:
                try:
                    stack.enter_context(exclusive_file_lock(self.directory / "access.lock"))
                    break
                except ConflictError:
                    if time.monotonic() >= deadline:
                        raise ConflictError("working context is busy; retry the operation") from None
                    time.sleep(0.05)
            self._check_paths()
            yield

    def reset(self) -> dict[str, Any]:
        """Remove only the known disposable cache files, even after corruption."""
        self._check_paths()
        if not self.directory.exists():
            return {"removed_files": 0, "enabled": False}
        with self._access():
            removed = 0
            for suffix in ("-journal", "-wal", "-shm", ""):
                path = Path(str(self.path) + suffix)
                if path.exists():
                    path.unlink()
                    removed += 1
            if os.name != "nt":
                descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        return {"removed_files": removed, "enabled": False}

    @contextmanager
    def connect(self, *, write: bool = False, create: bool = False) -> Iterator[sqlite3.Connection]:
        self._check_paths()
        if create:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            harden_private_path(self.directory, directory=True)
        elif not self.path.is_file():
            raise NotFoundError("working context is disabled; enable an approved source with the local CLI")
        with self._access(), self._connection(write=write, create=create) as db:
            yield db

    @contextmanager
    def _connection(self, *, write: bool, create: bool) -> Iterator[sqlite3.Connection]:
        initialize = create and (not self.path.exists() or self.path.stat().st_size == 0)
        if create:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
            harden_private_path(self.path, directory=False)
        if not self.path.is_file():
            raise NotFoundError("working context is disabled; enable an approved source with the local CLI")
        mode = "rw" if write or create else "ro"
        db = sqlite3.connect(f"{self.path.as_uri()}?mode={mode}", uri=True, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            if write or create:
                # DELETE avoids retaining raw source bodies in a long-lived WAL.
                db.execute("PRAGMA journal_mode=DELETE")
                db.execute("PRAGMA synchronous=FULL")
                db.execute("PRAGMA secure_delete=ON")
            if initialize:
                db.executescript(SCHEMA)
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT COUNT(*) FROM context_meta").fetchone()[0] == 0:
                    db.execute("INSERT INTO context_meta VALUES(1, ?)", (self.library_uid,))
                db.commit()
            row = db.execute("SELECT version, library_uid FROM context_meta").fetchone()
            if row is None or row["version"] != 1 or row["library_uid"] != self.library_uid:
                raise IntegrityCheckError("working context belongs to another library or an unsupported schema")
            if write or create:
                db.execute("BEGIN IMMEDIATE")
            else:
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
            yield db
            db.commit()
        except sqlite3.OperationalError as exc:
            db.rollback()
            if "locked" in str(exc).lower():
                raise ConflictError("working context is busy; retry the operation") from exc
            raise IntegrityCheckError("working context database could not complete the operation") from exc
        except sqlite3.DatabaseError as exc:
            db.rollback()
            raise IntegrityCheckError("working context database is damaged; use local context reset") from exc
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def enable(self, project: str, source_uid: str, consent_uid: str, ttl_days: int) -> dict[str, Any]:
        if not 1 <= ttl_days <= 30:
            raise ValidationError("ttl_days must be between 1 and 30")
        with self.connect(write=True, create=True) as db:
            count = db.execute("SELECT COUNT(*) FROM context_sources WHERE project=?", (project,)).fetchone()[0]
            existing = db.execute(
                "SELECT 1 FROM context_sources WHERE project=? AND source_uid=?", (project, source_uid)
            ).fetchone()
            if count >= 100 and not existing:
                raise ValidationError("a project can enable at most 100 temporary sources")
            # Re-enabling a revised consent never resurrects its old content.
            db.execute(
                "DELETE FROM artifacts WHERE project=? AND source_uid=? AND consent_uid<>?",
                (project, source_uid, consent_uid),
            )
            db.execute(
                "INSERT INTO context_sources VALUES(?,?,?,?) ON CONFLICT(project,source_uid) "
                "DO UPDATE SET consent_uid=excluded.consent_uid, ttl_days=excluded.ttl_days",
                (project, source_uid, consent_uid, ttl_days),
            )
        return {"project": project, "source_uid": source_uid, "ttl_days": ttl_days, "enabled": True}

    def sources(self, project: str) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM context_sources WHERE project=?", (project,))]

    def disable(self, project: str, source_uid: str | None = None) -> dict[str, Any]:
        if source_uid is not None:
            try:
                source_uid = str(uuid.UUID(source_uid))
            except ValueError as exc:
                raise ValidationError("source_uid must be a nonempty UUID; omit it to disable the project") from exc
        if not self.path.exists():
            return {"project": project, "removed": 0}
        predicate = "project=?" + (" AND source_uid=?" if source_uid is not None else "")
        args = (project, source_uid) if source_uid is not None else (project,)
        with self.connect(write=True) as db:
            removed = db.execute(f"DELETE FROM artifacts WHERE {predicate}", args).rowcount  # noqa: S608
            db.execute(f"DELETE FROM context_sources WHERE {predicate}", args)  # noqa: S608
            db.execute("INSERT INTO context_fts(context_fts) VALUES('optimize')")
        return {"project": project, "removed": removed}

    def forget_source(self, source_uid: str) -> None:
        if not self.path.exists():
            return
        with self.connect(write=True) as db:
            db.execute("DELETE FROM artifacts WHERE source_uid=?", (source_uid,))
            db.execute("DELETE FROM context_sources WHERE source_uid=?", (source_uid,))
            db.execute("INSERT INTO context_fts(context_fts) VALUES('optimize')")

    def clean(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"removed": 0}
        with self.connect(write=True) as db:
            removed = db.execute("DELETE FROM artifacts WHERE expires_at<=?", (time.time(),)).rowcount
            db.execute("INSERT INTO context_fts(context_fts) VALUES('optimize')")
        return {"removed": removed}

    def index(self, *, project: str, source: dict[str, Any], locator: str, label: str, content: str) -> dict[str, Any]:
        raw = content.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        chunks = chunk_text(content)
        now = time.time()
        with self.connect(write=True) as db:
            db.execute("DELETE FROM artifacts WHERE expires_at<=?", (now,))
            enabled = db.execute(
                "SELECT consent_uid, ttl_days FROM context_sources WHERE project=? AND source_uid=?",
                (project, source["source_uid"]),
            ).fetchone()
            if enabled is None or enabled[0] != source["consent_uid"]:
                raise ValidationError("source content storage is no longer enabled")
            old = db.execute(
                "SELECT artifact_uid FROM artifacts WHERE project=? AND source_uid=? AND source_locator=?",
                (project, source["source_uid"], locator),
            ).fetchone()
            if old:
                db.execute("DELETE FROM artifacts WHERE artifact_uid=?", (old[0],))
            used, count = db.execute(
                "SELECT COALESCE(SUM(byte_count),0), COUNT(*) FROM artifacts WHERE project=?", (project,)
            ).fetchone()
            if used + len(raw) > MAX_PROJECT_BYTES or count >= MAX_PROJECT_DOCUMENTS:
                raise ValidationError("project working-context quota exceeded; remove inputs or let them expire")
            uid = str(uuid.uuid4())
            expires = now + int(enabled[1]) * 86400
            db.execute(
                "INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uid,
                    project,
                    source["source_uid"],
                    source["consent_uid"],
                    locator,
                    label,
                    digest,
                    content,
                    len(raw),
                    len(content.splitlines()),
                    now,
                    expires,
                ),
            )
            db.executemany(
                "INSERT INTO context_chunks(artifact_uid,start_line,end_line,content,terms) VALUES(?,?,?,?,?)",
                ((uid, start, end, text, " ".join(search_tokens(text))) for start, end, text in chunks),
            )
        return {
            "artifact_uid": uid,
            "project": project,
            "source_uid": source["source_uid"],
            "source_locator": locator,
            "sha256": digest,
            "indexed_bytes": len(raw),
            "chunks": len(chunks),
            "expires_at": expires,
            "status": "temporary",
            "next": "memory_recall or memory_context_read",
        }

    def search(self, project: str, query: str, allowed: dict[str, str], limit: int) -> list[dict[str, Any]]:
        tokens = search_tokens(query)[:32]
        if not tokens or not allowed or not self.path.exists():
            return []
        match = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        conditions = " OR ".join("(a.source_uid=? AND a.consent_uid=?)" for _ in allowed)
        params = [value for pair in allowed.items() for value in pair]
        with self.connect() as db:
            rows = db.execute(
                "SELECT a.artifact_uid,a.source_uid,a.source_locator,a.label,a.sha256,a.created_at,a.expires_at, "  # noqa: S608
                "c.start_line,c.end_line,c.content,bm25(context_fts) AS rank "
                "FROM context_fts JOIN context_chunks c ON c.chunk_id=context_fts.rowid "
                "JOIN artifacts a ON a.artifact_uid=c.artifact_uid "
                f"WHERE context_fts MATCH ? AND a.project=? AND a.expires_at>? AND ({conditions}) "
                "ORDER BY rank,c.chunk_id LIMIT ?",
                (match, project, time.time(), *params, limit),
            ).fetchall()
        results = []
        for row in rows:
            item = {**dict(row), "kind": "context", "status": "temporary"}
            text = item["content"]
            if len(text) > 800:
                folded = text.casefold()
                positions = [folded.find(token) for token in tokens if token in folded]
                start = max(0, min(positions, default=0) - 160)
                start = min(start, len(text) - 800)
                item["excerpt_start_line"] = item["start_line"] + text[:start].count("\n")
                item["content"] = text[start : start + 800]
                item["content_truncated"] = True
            results.append(item)
        return results

    def read(self, project: str, artifact_uid: str, allowed: dict[str, str]) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM artifacts WHERE artifact_uid=? AND project=? AND expires_at>?",
                (artifact_uid, project, time.time()),
            ).fetchone()
            if row is None or allowed.get(row["source_uid"]) != row["consent_uid"]:
                raise NotFoundError("working document is unavailable or expired; index the approved source again")
            return dict(row)
