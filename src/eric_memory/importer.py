"""Read-only import from a Holograph memory_store.db. Never writes the source."""

from __future__ import annotations

import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .content_policy import inspect_content
from .entities import normalize_names
from .paths import require_absolute
from .scopes import scope_from_tags
from .store import MemoryStore, today_utc

STATUS_RE = re.compile(r"(?:^|,)status:([a-zA-Z0-9_-]+)")
DEPRECATED_RE = re.compile(r"(?:^|,)status:deprecated(?:,|$)")


def parse_status_from_tags(tags: str | None) -> str | None:
    if not tags:
        return None
    if DEPRECATED_RE.search(tags):
        return "deprecated"
    if STATUS_RE.search(tags):
        return "active"
    return None


def open_holograph_readonly(source: str | Path) -> sqlite3.Connection:
    path = require_absolute(source, name="holograph db")
    if not path.is_file():
        raise FileNotFoundError(f"holograph database not found: {path}")
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _entity_names(conn: sqlite3.Connection, fact_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT e.name FROM entities e
        JOIN fact_entities fe ON fe.entity_id = e.entity_id
        WHERE fe.fact_id = ?
        """,
        (fact_id,),
    ).fetchall()
    return normalize_names([r["name"] for r in rows])


def import_holograph(
    store: MemoryStore,
    source: str | Path,
    *,
    actor: str = "cli",
    as_of: str | None = None,
) -> dict[str, Any]:
    """Copy facts and entities. Do not guess deprecation for untagged rows."""
    import_day = as_of or today_utc()
    source_path = require_absolute(source, name="holograph db")
    conn = open_holograph_readonly(source_path)
    added = 0
    skipped = 0
    linked_existing = 0
    deprecated = 0
    untagged_active = 0
    repaired = 0
    policy_quarantined = 0
    policy_rejected = 0
    errors: list[dict[str, Any]] = []
    import_source = store.ensure_import_source(source_path, actor=actor)
    source_uid = str(import_source["source_uid"])
    source_id = int(
        store.connection.execute("SELECT source_id FROM sources WHERE source_uid = ?", (source_uid,)).fetchone()[0]
    )
    try:
        facts = conn.execute(
            """
            SELECT fact_id, content, category, tags, trust_score, created_at, updated_at
            FROM facts
            ORDER BY fact_id
            """
        ).fetchall()
        for row in facts:
            source_ref = f"holograph:{row['fact_id']}"
            raw_content = str(row["content"])
            policy = inspect_content(raw_content)
            if policy.action == "reject":
                policy_rejected += 1
                errors.append(
                    {
                        "source_fact_id": int(row["fact_id"]),
                        "error": "CONTENT_REJECTED",
                        "policy_codes": list(policy.codes),
                    }
                )
                continue
            if policy.action == "quarantine":
                candidate, created = store.add_candidate(
                    submission_uid=str(uuid.uuid5(uuid.UUID(source_uid), source_ref)),
                    principal_key="local",
                    content=None,
                    content_summary=policy.safe_summary,
                    category=str(row["category"] or "general"),
                    entities=_entity_names(conn, int(row["fact_id"])),
                    as_of=import_day,
                    confidence=float(row["trust_score"] or 0.5),
                    scope=scope_from_tags(str(row["tags"] or ""))[0],
                    source_uid=source_uid,
                    source_locator=source_ref,
                    manual_input=False,
                    status="quarantined",
                    policy_codes=list(policy.codes),
                    actor=actor,
                )
                del candidate
                if created:
                    policy_quarantined += 1
                else:
                    skipped += 1
                continue
            linked = store.connection.execute(
                """
                SELECT f.fact_id, f.status FROM fact_sources fs
                JOIN facts f ON f.fact_id = fs.fact_id
                WHERE fs.source_id = ? AND fs.source_locator = ?
                """,
                (source_id, source_ref),
            ).fetchone()
            existing = store.connection.execute(
                "SELECT fact_id, status FROM facts WHERE source_kind = 'import' AND source_ref = ?",
                (source_ref,),
            ).fetchone()
            existing = existing or linked
            if existing:
                skipped += 1
                tag_status = parse_status_from_tags(row["tags"] or "")
                if tag_status == "deprecated" and existing["status"] != "deprecated":
                    store.deprecate(
                        int(existing["fact_id"]),
                        reason="import repair: source tags contain status:deprecated",
                        actor=actor,
                    )
                    repaired += 1
                    deprecated += 1
                continue
            tag_status = parse_status_from_tags(row["tags"] or "")
            if tag_status is None:
                status = "active"
                fact_as_of = import_day
                untagged_active += 1
            else:
                status = tag_status
                fact_as_of = import_day if status == "active" else (str(row["updated_at"] or import_day)[:10])
            names = _entity_names(conn, int(row["fact_id"]))
            try:
                fact, created = store.add_fact_with_result(
                    raw_content,
                    category=row["category"] or "general",
                    tags=row["tags"] or "",
                    trust=float(row["trust_score"] or 0.5),
                    as_of=fact_as_of,
                    entities=names,
                    source_kind="import",
                    source_ref=source_ref,
                    source_uid=source_uid,
                    source_locator=source_ref,
                    actor=actor,
                    status=status,
                    created_at=str(row["created_at"] or ""),
                )
                if created:
                    added += 1
                else:
                    linked_existing += 1
                if fact.status == "deprecated":
                    deprecated += 1
            except (ValueError, KeyError, sqlite3.DatabaseError) as exc:
                errors.append({"source_fact_id": int(row["fact_id"]), "error": type(exc).__name__})
    finally:
        conn.close()
    return {
        "source": str(source_path),
        "added": added,
        "skipped": skipped,
        "linked_existing": linked_existing,
        "deprecated": deprecated,
        "repaired": repaired,
        "policy_quarantined": policy_quarantined,
        "policy_rejected": policy_rejected,
        "untagged_marked_active": untagged_active,
        "errors": errors,
        "error_count": len(errors),
        "as_of": import_day,
        "counts": store.counts(),
    }
