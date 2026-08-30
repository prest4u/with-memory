"""Read-only import from a Holograph memory_store.db. Never writes the source."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .entities import normalize_names
from .paths import require_absolute
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
) -> dict:
    """Copy facts and entities. Do not guess deprecation for untagged rows."""
    import_day = as_of or today_utc()
    source_path = require_absolute(source, name="holograph db")
    conn = open_holograph_readonly(source_path)
    added = 0
    skipped = 0
    deprecated = 0
    untagged_active = 0
    repaired = 0
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
            existing = store.connection.execute(
                "SELECT fact_id, status FROM facts WHERE source_kind = 'import' AND source_ref = ?",
                (source_ref,),
            ).fetchone()
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
            fact = store.add_fact(
                row["content"],
                category=row["category"] or "general",
                tags=row["tags"] or "",
                trust=float(row["trust_score"] or 0.5),
                as_of=fact_as_of,
                entities=names,
                source_kind="import",
                source_ref=source_ref,
                actor=actor,
                status=status,
                created_at=str(row["created_at"] or ""),
            )
            added += 1
            if fact.status == "deprecated":
                deprecated += 1
    finally:
        conn.close()
    return {
        "source": str(source_path),
        "added": added,
        "skipped": skipped,
        "deprecated": deprecated,
        "repaired": repaired,
        "untagged_marked_active": untagged_active,
        "as_of": import_day,
        "counts": store.counts(),
    }
