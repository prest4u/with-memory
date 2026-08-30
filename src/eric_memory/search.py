"""Hybrid retrieval: entity first; CJK uses LIKE; Latin uses FTS5 then LIKE. Default hides deprecated."""

from __future__ import annotations

import re
import sqlite3

from .entities import has_cjk
from .models import Fact
from .store import MemoryStore

TOKEN_SPLIT = re.compile(r"[\s,，;；]+")
CJK_ASCII_SPLIT = re.compile(r"[\u3400-\u9fff]+|[A-Za-z]+|\d+")


def tokenize_like(query: str) -> list[str]:
    parts = [p.strip() for p in TOKEN_SPLIT.split(query) if p.strip()]
    if not parts:
        parts = [query.strip()]
    tokens: list[str] = []
    seen: set[str] = set()
    for part in parts:
        pieces = [part]
        if has_cjk(part) and re.search(r"[A-Za-z0-9]", part):
            found = CJK_ASCII_SPLIT.findall(part)
            if len(found) > 1:
                pieces = found
        for token in pieces:
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)
    return tokens


def _status_clause(include_deprecated: bool) -> str:
    return "" if include_deprecated else "AND f.status = 'active'"


def _scope_clause(scope: str, project: str | None, workspace: str | None) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    scope = (scope or "user").strip().lower()
    if scope == "project" or project:
        value = (project or "").strip()
        if value:
            clauses.append("(f.tags LIKE ? OR f.category = ? OR f.content LIKE ?)")
            params.extend([f"%{value}%", value, f"%{value}%"])
    if scope == "workspace" or workspace:
        value = (workspace or "").strip()
        if value:
            clauses.append("(f.tags LIKE ? OR f.content LIKE ?)")
            params.extend([f"%{value}%", f"%{value}%"])
    if not clauses:
        return "", []
    return "AND " + " AND ".join(clauses), params


def _rows_to_facts(store: MemoryStore, rows: list[sqlite3.Row]) -> list[Fact]:
    return [store._row_to_fact(row) for row in rows]


def search_entity(
    store: MemoryStore,
    query: str,
    *,
    limit: int,
    include_deprecated: bool,
    extra_sql: str,
    extra_params: list,
) -> list[Fact]:
    sql = f"""
        SELECT DISTINCT f.*
        FROM facts f
        JOIN fact_entities fe ON fe.fact_id = f.fact_id
        JOIN entities e ON e.entity_id = fe.entity_id
        WHERE (e.name = ? OR e.name LIKE ? OR e.name_key = ?)
          {_status_clause(include_deprecated)}
          {extra_sql}
        ORDER BY (f.status = 'active') DESC, f.trust DESC, f.fact_id DESC
        LIMIT ?
    """
    params = [query, f"%{query}%", query.casefold(), *extra_params, limit]
    with store._lock:
        rows = store.connection.execute(sql, params).fetchall()
    return _rows_to_facts(store, rows)


def search_fts(
    store: MemoryStore,
    query: str,
    *,
    limit: int,
    include_deprecated: bool,
    extra_sql: str,
    extra_params: list,
) -> list[Fact]:
    sql = f"""
        SELECT f.*
        FROM facts_fts
        JOIN facts f ON f.fact_id = facts_fts.rowid
        WHERE facts_fts MATCH ?
          {_status_clause(include_deprecated)}
          {extra_sql}
        ORDER BY (f.status = 'active') DESC, f.trust DESC, f.fact_id DESC
        LIMIT ?
    """
    try:
        with store._lock:
            rows = store.connection.execute(sql, (query, *extra_params, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    return _rows_to_facts(store, rows)


def search_like(
    store: MemoryStore,
    query: str,
    *,
    limit: int,
    include_deprecated: bool,
    extra_sql: str,
    extra_params: list,
) -> list[Fact]:
    tokens = tokenize_like(query)
    if not tokens:
        return []
    where = " AND ".join(["(f.content LIKE ? OR f.tags LIKE ?)"] * len(tokens))
    params: list = []
    for token in tokens:
        params.extend([f"%{token}%", f"%{token}%"])
    sql = f"""
        SELECT f.*
        FROM facts f
        WHERE {where}
          {_status_clause(include_deprecated)}
          {extra_sql}
        ORDER BY (f.status = 'active') DESC, f.trust DESC, f.fact_id DESC
        LIMIT ?
    """
    with store._lock:
        rows = store.connection.execute(sql, (*params, *extra_params, limit)).fetchall()
    return _rows_to_facts(store, rows)


def search_facts(
    store: MemoryStore,
    query: str,
    *,
    limit: int = 10,
    include_deprecated: bool = False,
    scope: str = "user",
    project: str | None = None,
    workspace: str | None = None,
) -> dict:
    query = query.strip()
    if not query:
        return {"layer": "empty", "query": query, "include_deprecated": include_deprecated, "facts": []}
    extra_sql, extra_params = _scope_clause(scope, project, workspace)
    kwargs = {
        "limit": limit,
        "include_deprecated": include_deprecated,
        "extra_sql": extra_sql,
        "extra_params": extra_params,
    }
    entity_hits = search_entity(store, query, **kwargs)
    if entity_hits:
        return {
            "layer": "entity",
            "query": query,
            "include_deprecated": include_deprecated,
            "facts": entity_hits,
        }
    if has_cjk(query):
        like_hits = search_like(store, query, **kwargs)
        return {
            "layer": "like-fallback",
            "query": query,
            "include_deprecated": include_deprecated,
            "facts": like_hits,
        }
    fts_hits = search_fts(store, query, **kwargs)
    if fts_hits:
        return {
            "layer": "fts5",
            "query": query,
            "include_deprecated": include_deprecated,
            "facts": fts_hits,
        }
    like_hits = search_like(store, query, **kwargs)
    return {
        "layer": "like-fallback",
        "query": query,
        "include_deprecated": include_deprecated,
        "facts": like_hits,
    }
