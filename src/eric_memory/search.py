"""SQLite-only hybrid retrieval with ACL/scope/status filtering before ranking."""

from __future__ import annotations

import math
import re
import sqlite3
import unicodedata
from collections import defaultdict
from typing import Any

from .entities import has_cjk
from .errors import ValidationError
from .models import Fact
from .permissions import FACT_SEARCH, HISTORY_READ
from .scopes import ScopeSpec, scope_from_request
from .store import MemoryStore, search_terms

TOKEN_SPLIT = re.compile(r"[\s,，;；]+")
CJK_ASCII_SPLIT = re.compile(r"[\u3400-\u9fff]+|[A-Za-z]+|\d+")
RRF_K = 60
WEIGHTS = {
    "exact_entity": 3.0,
    "entity_alias": 2.7,
    "search_terms": 1.8,
    "fts5": 1.4,
    "like_fallback": 1.0,
}


def tokenize_like(query: str) -> list[str]:
    parts = [part.strip() for part in TOKEN_SPLIT.split(query) if part.strip()]
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
            normalized = unicodedata.normalize("NFKC", token).casefold()
            if normalized and normalized not in seen:
                seen.add(normalized)
                tokens.append(normalized)
    return tokens


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _validate(query: str, limit: int) -> str:
    value = query.strip()
    if len(value) > 256:
        raise ValidationError("query must be at most 256 Unicode characters")
    if not 1 <= int(limit) <= 100:
        raise ValidationError("limit must be between 1 and 100")
    return value


def _filter_sql(
    store: MemoryStore,
    *,
    include_deprecated: bool,
    requested_scope: ScopeSpec,
    principal_key: str,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if not include_deprecated:
        clauses.append("f.status = 'active'")

    accessible = store.accessible_scope_ids(principal_key, FACT_SEARCH)
    if accessible is not None:
        if not accessible:
            clauses.append("0 = 1")
        else:
            placeholders = ",".join("?" for _ in accessible)
            clauses.append(
                # Only generated '?' placeholders are interpolated; IDs stay bound parameters.
                f"EXISTS (SELECT 1 FROM fact_scopes acl_fs WHERE acl_fs.fact_id = f.fact_id "  # noqa: S608
                f"AND acl_fs.scope_id IN ({placeholders}))"
            )
            params.extend(sorted(accessible))

    # history:read gates only deprecated rows. Active facts retain their normal
    # fact:search visibility when a caller explicitly asks for mixed history.
    if include_deprecated:
        history_accessible = store.accessible_scope_ids(principal_key, HISTORY_READ)
        if history_accessible is not None:
            if not history_accessible:
                clauses.append("f.status = 'active'")
            else:
                placeholders = ",".join("?" for _ in history_accessible)
                clauses.append(
                    "(f.status = 'active' OR EXISTS (SELECT 1 FROM fact_scopes hist_fs "  # noqa: S608
                    # Only generated '?' placeholders are interpolated; IDs stay bound parameters.
                    f"WHERE hist_fs.fact_id = f.fact_id AND hist_fs.scope_id IN ({placeholders})))"
                )
                params.extend(sorted(history_accessible))

    if requested_scope.kind != "user":
        clauses.append(
            "EXISTS (SELECT 1 FROM fact_scopes req_fs JOIN scopes req_s "
            "ON req_s.scope_id = req_fs.scope_id WHERE req_fs.fact_id = f.fact_id "
            "AND req_s.fingerprint = ?)"
        )
        params.append(requested_scope.fingerprint)
    return (" AND ".join(clauses) if clauses else "1 = 1"), params


def _ids(store: MemoryStore, sql: str, params: list[Any]) -> list[int]:
    try:
        rows = store.connection.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    return [int(row[0]) for row in rows]


def _term_rankings(
    store: MemoryStore,
    terms: list[tuple[str, str]],
    *,
    filter_sql: str,
    filter_params: list[Any],
    pool: int,
) -> list[int]:
    """Anchor on rare terms, then score coverage only inside the filtered candidate set."""
    if not terms:
        return []
    threshold = max(1, math.ceil(len(terms) * 0.6))
    # CJK bigrams and Latin tokens are more selective than CJK unigrams. The
    # cap also keeps the query below SQLite's conservative parameter limit.
    sampled = sorted(terms, key=lambda item: (item[1] == "cjk1", item[0], item[1]))[:64]
    frequency_clauses = " OR ".join("(term = ? AND kind = ?)" for _ in sampled)
    frequency_params = [value for pair in sampled for value in pair]
    frequency_rows = store.connection.execute(
        f"SELECT term, kind, COUNT(*) AS documents FROM fact_search_terms "  # noqa: S608
        f"WHERE {frequency_clauses} GROUP BY term, kind",
        frequency_params,
    ).fetchall()
    frequencies = {(str(row["term"]), str(row["kind"])): int(row["documents"]) for row in frequency_rows}
    available = sorted(
        (pair for pair in sampled if frequencies.get(pair, 0) > 0),
        key=lambda pair: (frequencies[pair], pair[1] == "cjk1", pair[0], pair[1]),
    )
    if not available:
        return []
    anchor_count = min(len(available), max(1, len(terms) - threshold + 1), 32)
    anchors = available[:anchor_count]
    anchor_clauses = " OR ".join("(fst.term = ? AND fst.kind = ?)" for _ in anchors)
    anchor_params = [value for pair in anchors for value in pair]
    candidate_cap = min(2000, max(pool * 4, 200))
    candidates = store.connection.execute(
        f"SELECT DISTINCT f.fact_id, f.trust FROM facts f "  # noqa: S608
        "JOIN fact_search_terms fst ON fst.fact_id = f.fact_id "
        f"WHERE {filter_sql} AND ({anchor_clauses}) "
        "ORDER BY f.trust DESC, f.fact_id DESC LIMIT ?",
        [*filter_params, *anchor_params, candidate_cap],
    ).fetchall()
    if not candidates:
        return []
    trust = {int(row["fact_id"]): float(row["trust"]) for row in candidates}
    candidate_ids = list(trust)
    query_terms = set(terms)
    matches: dict[int, int] = defaultdict(int)
    for start in range(0, len(candidate_ids), 800):
        batch = candidate_ids[start : start + 800]
        placeholders = ",".join("?" for _ in batch)
        rows = store.connection.execute(
            f"SELECT fact_id, term, kind FROM fact_search_terms "  # noqa: S608 - placeholders only
            f"WHERE fact_id IN ({placeholders})",
            batch,
        ).fetchall()
        for row in rows:
            if (str(row["term"]), str(row["kind"])) in query_terms:
                matches[int(row["fact_id"])] += 1
    eligible = (fact_id for fact_id in candidate_ids if matches[fact_id] >= threshold)
    return sorted(eligible, key=lambda fact_id: (-matches[fact_id], -trust[fact_id], -fact_id))[:pool]


def _legacy_search(
    store: MemoryStore,
    query: str,
    *,
    limit: int,
    include_deprecated: bool,
    scope: ScopeSpec,
) -> dict[str, Any]:
    clauses = ["1 = 1"]
    params: list[Any] = []
    if not include_deprecated:
        clauses.append("f.status = 'active'")
    if scope.kind != "user":
        pattern = f"%{_escape_like(f'{scope.kind}:{scope.key}')}%"
        clauses.append("f.tags LIKE ? ESCAPE '\\'")
        params.append(pattern)
    base = " AND ".join(clauses)
    pool = min(500, max(limit * 8, 40))
    normalized = unicodedata.normalize("NFKC", query).casefold()
    entity_ids = _ids(
        store,
        f"SELECT DISTINCT f.fact_id FROM facts f "  # noqa: S608 - fixed predicates/placeholders
        "JOIN fact_entities fe ON fe.fact_id = f.fact_id "
        "JOIN entities e ON e.entity_id = fe.entity_id "
        f"WHERE {base} AND (e.name_key = ? OR e.name LIKE ? ESCAPE '\\') "
        "ORDER BY f.trust DESC, f.fact_id DESC LIMIT ?",
        [*params, normalized, f"%{_escape_like(query)}%", pool],
    )
    tokens = tokenize_like(query)
    like_where = " AND ".join(["(f.content LIKE ? ESCAPE '\\' OR f.tags LIKE ? ESCAPE '\\')"] * len(tokens))
    like_params: list[Any] = []
    for token in tokens:
        pattern = f"%{_escape_like(token)}%"
        like_params.extend([pattern, pattern])
    like_ids = (
        _ids(
            store,
            f"SELECT f.fact_id FROM facts f WHERE {base} AND {like_where} "  # noqa: S608
            "ORDER BY f.trust DESC, f.fact_id DESC LIMIT ?",
            [*params, *like_params, pool],
        )
        if tokens
        else []
    )
    rankings = {"exact_entity": entity_ids, "like_fallback": like_ids}
    facts = _fuse(store, rankings, limit)
    layer = "entity" if entity_ids else "like-fallback"
    return {
        "layer": layer,
        "rank_version": "rrf-v1-legacy",
        "query": query,
        "include_deprecated": include_deprecated,
        "scope": scope.to_dict(),
        "facts": facts,
    }


def _fuse(store: MemoryStore, rankings: dict[str, list[int]], limit: int) -> list[Fact]:
    score: dict[int, float] = defaultdict(float)
    matched: dict[int, list[str]] = defaultdict(list)
    for channel, ids in rankings.items():
        for rank, fact_id in enumerate(ids, start=1):
            score[fact_id] += WEIGHTS[channel] / (RRF_K + rank)
            matched[fact_id].append(channel)
    ordered = sorted(score, key=lambda fact_id: (-score[fact_id], fact_id))[:limit]
    if not ordered:
        return []
    placeholders = ",".join("?" for _ in ordered)
    rows = store.connection.execute(
        f"SELECT * FROM facts WHERE fact_id IN ({placeholders})",  # noqa: S608 - placeholders only
        ordered,
    ).fetchall()
    by_id = {int(row["fact_id"]): row for row in rows}
    result: list[Fact] = []
    for fact_id in ordered:
        row = by_id.get(fact_id)
        if row is None:
            continue
        fact = store._row_to_fact(row)
        fact.score = round(score[fact_id], 8)
        fact.matched_by = matched[fact_id]
        result.append(fact)
    return result


def search_facts(
    store: MemoryStore,
    query: str,
    *,
    limit: int = 10,
    include_deprecated: bool = False,
    scope: str = "user",
    project: str | None = None,
    workspace: str | None = None,
    principal_key: str = "local",
) -> dict[str, Any]:
    value = _validate(query, limit)
    requested_scope = scope_from_request(scope, project=project, workspace=workspace)
    if not value:
        return {
            "layer": "empty",
            "rank_version": "rrf-v1",
            "query": value,
            "include_deprecated": include_deprecated,
            "scope": requested_scope.to_dict(),
            "facts": [],
        }
    if store.schema_version < 2:
        return _legacy_search(
            store,
            value,
            limit=limit,
            include_deprecated=include_deprecated,
            scope=requested_scope,
        )

    filter_sql, filter_params = _filter_sql(
        store,
        include_deprecated=include_deprecated,
        requested_scope=requested_scope,
        principal_key=principal_key,
    )
    pool = min(500, max(limit * 8, 40))
    normalized = unicodedata.normalize("NFKC", value).casefold()

    exact_entity = _ids(
        store,
        f"SELECT DISTINCT f.fact_id FROM facts f "  # noqa: S608 - fixed predicates/placeholders
        "JOIN fact_entities fe ON fe.fact_id = f.fact_id "
        "JOIN entities e ON e.entity_id = fe.entity_id "
        f"WHERE {filter_sql} AND e.name_key = ? "
        "ORDER BY f.trust DESC, f.fact_id DESC LIMIT ?",
        [*filter_params, normalized, pool],
    )
    aliases = _ids(
        store,
        f"SELECT DISTINCT f.fact_id FROM facts f "  # noqa: S608 - fixed predicates/placeholders
        "JOIN fact_entities fe ON fe.fact_id = f.fact_id "
        "JOIN entity_aliases ea ON ea.entity_id = fe.entity_id "
        f"WHERE {filter_sql} AND ea.alias_key = ? "
        "ORDER BY f.trust DESC, f.fact_id DESC LIMIT ?",
        [*filter_params, normalized, pool],
    )

    terms = sorted(search_terms(value))
    term_ids = _term_rankings(
        store,
        terms,
        filter_sql=filter_sql,
        filter_params=filter_params,
        pool=pool,
    )

    token_values = tokenize_like(value)
    fts_ids: list[int] = []
    if token_values:
        fts_query = " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in token_values)
        fts_ids = _ids(
            store,
            f"SELECT f.fact_id FROM facts_fts "  # noqa: S608 - fixed predicates/placeholders
            "JOIN facts f ON f.fact_id = facts_fts.rowid "
            f"WHERE {filter_sql} AND facts_fts MATCH ? "
            "ORDER BY bm25(facts_fts), f.trust DESC, f.fact_id DESC LIMIT ?",
            [*filter_params, fts_query, pool],
        )

    like_ids: list[int] = []
    if token_values:
        like_where = " AND ".join(["(f.content LIKE ? ESCAPE '\\' OR f.tags LIKE ? ESCAPE '\\')"] * len(token_values))
        like_params: list[Any] = []
        for token in token_values:
            pattern = f"%{_escape_like(token)}%"
            like_params.extend([pattern, pattern])
        like_ids = _ids(
            store,
            f"SELECT f.fact_id FROM facts f "  # noqa: S608 - fixed predicates/placeholders
            f"WHERE {filter_sql} AND {like_where} "
            "ORDER BY f.trust DESC, f.fact_id DESC LIMIT ?",
            [*filter_params, *like_params, pool],
        )

    rankings = {
        "exact_entity": exact_entity,
        "entity_alias": aliases,
        "search_terms": term_ids,
        "fts5": fts_ids,
        "like_fallback": like_ids,
    }
    facts = _fuse(store, rankings, limit)
    if exact_entity or aliases:
        layer = "entity"
    elif fts_ids:
        layer = "fts5"
    else:
        layer = "like-fallback"
    return {
        "layer": layer,
        "rank_version": "rrf-v1",
        "query": value,
        "include_deprecated": include_deprecated,
        "scope": requested_scope.to_dict(),
        "facts": facts,
    }
