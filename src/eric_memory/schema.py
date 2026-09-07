"""SQLite schema owned by With.

Schema v2 keeps every v1 table/column that was part of the public surface while
adding stable identifiers, structured scopes, candidates, consented sources,
principals, append-only metadata events, and incremental scan state.
"""

from __future__ import annotations

SCHEMA_VERSION = 2
SEARCH_RANK_VERSION = "rrf-v1"

SCHEMA_V2 = r"""
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS libraries (
    library_uid TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    device_uid  TEXT PRIMARY KEY,
    library_uid TEXT NOT NULL REFERENCES libraries(library_uid),
    label       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scopes (
    scope_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_uid    TEXT NOT NULL UNIQUE,
    scope_kind   TEXT NOT NULL CHECK (scope_kind IN ('user', 'project', 'workspace')),
    scope_key    TEXT NOT NULL DEFAULT '',
    fingerprint  TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL,
    UNIQUE(scope_kind, scope_key)
);

CREATE TABLE IF NOT EXISTS facts (
    fact_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_uid                TEXT NOT NULL UNIQUE,
    content                 TEXT NOT NULL,
    normalized_content_hash TEXT NOT NULL,
    category                TEXT NOT NULL DEFAULT 'general',
    tags                    TEXT NOT NULL DEFAULT '',
    trust                   REAL NOT NULL DEFAULT 0.5 CHECK (trust >= 0 AND trust <= 1),
    status                  TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'deprecated')),
    as_of                   TEXT NOT NULL,
    superseded_by           INTEGER REFERENCES facts(fact_id),
    source_kind             TEXT NOT NULL DEFAULT 'manual',
    source_ref              TEXT NOT NULL DEFAULT '',
    scope_fingerprint       TEXT NOT NULL DEFAULT 'user:',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_active_scope_content
    ON facts(normalized_content_hash, scope_fingerprint) WHERE status = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_source_compat
    ON facts(source_kind, source_ref) WHERE source_ref != '';
CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status);
CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
CREATE INDEX IF NOT EXISTS idx_facts_as_of ON facts(as_of);
CREATE INDEX IF NOT EXISTS idx_facts_scope ON facts(scope_fingerprint, status);

CREATE TRIGGER IF NOT EXISTS facts_content_immutable
BEFORE UPDATE OF content ON facts
WHEN old.content <> new.content
BEGIN
    SELECT RAISE(ABORT, 'fact content is immutable');
END;

CREATE TABLE IF NOT EXISTS entities (
    entity_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    name_key    TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL DEFAULT 'unknown',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

CREATE TABLE IF NOT EXISTS entity_aliases (
    alias_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id  INTEGER NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    alias      TEXT NOT NULL,
    alias_key  TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_entities (
    fact_id   INTEGER NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    PRIMARY KEY (fact_id, entity_id)
);

CREATE TABLE IF NOT EXISTS fact_scopes (
    fact_id  INTEGER NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    scope_id INTEGER NOT NULL REFERENCES scopes(scope_id),
    PRIMARY KEY (fact_id, scope_id)
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
    indexed_at TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'current' CHECK (status IN ('current', 'stale')),
    source_uid TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder);
CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);
CREATE INDEX IF NOT EXISTS idx_files_current ON files(status, name);

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

CREATE TABLE IF NOT EXISTS principals (
    principal_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    principal_uid TEXT NOT NULL UNIQUE,
    key           TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    local_admin   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS principal_grants (
    grant_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    principal_id  INTEGER NOT NULL REFERENCES principals(principal_id) ON DELETE CASCADE,
    capability    TEXT NOT NULL,
    scope_id      INTEGER REFERENCES scopes(scope_id),
    created_at    TEXT NOT NULL,
    UNIQUE(principal_id, capability, scope_id)
);

CREATE INDEX IF NOT EXISTS idx_grants_principal ON principal_grants(principal_id, capability);

CREATE TABLE IF NOT EXISTS sources (
    source_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    source_uid      TEXT NOT NULL UNIQUE,
    source_kind     TEXT NOT NULL DEFAULT 'folder',
    canonical_root  TEXT NOT NULL,
    include_json    TEXT NOT NULL DEFAULT '[]',
    exclude_json    TEXT NOT NULL DEFAULT '[]',
    file_types_json TEXT NOT NULL DEFAULT '[]',
    max_file_bytes  INTEGER NOT NULL,
    harness_key     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'approved'
                    CHECK (status IN ('approved', 'revoked')),
    approved_at     TEXT NOT NULL,
    revoked_at      TEXT,
    consent_uid     TEXT NOT NULL,
    last_cursor     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS source_files (
    source_file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_uid       TEXT NOT NULL UNIQUE,
    source_id      INTEGER NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    path           TEXT NOT NULL,
    name           TEXT NOT NULL,
    sha256         TEXT NOT NULL,
    size           INTEGER NOT NULL,
    mtime_ns       INTEGER NOT NULL,
    status         TEXT NOT NULL DEFAULT 'current' CHECK (status IN ('current', 'stale')),
    last_seen_run  TEXT NOT NULL DEFAULT '',
    indexed_at     TEXT NOT NULL,
    UNIQUE(source_id, path)
);

CREATE INDEX IF NOT EXISTS idx_source_files_source_status
    ON source_files(source_id, status);

CREATE TABLE IF NOT EXISTS scan_runs (
    run_uid        TEXT PRIMARY KEY,
    source_id      INTEGER NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    principal_id   INTEGER REFERENCES principals(principal_id),
    status         TEXT NOT NULL CHECK (status IN ('open', 'complete', 'revoked', 'error')),
    started_at     TEXT NOT NULL,
    completed_at   TEXT,
    cursor_before  TEXT NOT NULL DEFAULT '',
    cursor_after   TEXT NOT NULL DEFAULT '',
    changed_count  INTEGER NOT NULL DEFAULT 0,
    unchanged_count INTEGER NOT NULL DEFAULT 0,
    stale_count    INTEGER NOT NULL DEFAULT 0,
    truncated      INTEGER NOT NULL DEFAULT 0,
    error_code     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS fact_sources (
    fact_id        INTEGER NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    source_id      INTEGER NOT NULL REFERENCES sources(source_id),
    source_locator TEXT NOT NULL DEFAULT '',
    linked_at      TEXT NOT NULL,
    PRIMARY KEY (fact_id, source_id, source_locator)
);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_uid     TEXT NOT NULL UNIQUE,
    submission_uid    TEXT NOT NULL,
    principal_id      INTEGER NOT NULL REFERENCES principals(principal_id),
    scope_id          INTEGER NOT NULL REFERENCES scopes(scope_id),
    source_id         INTEGER REFERENCES sources(source_id),
    source_locator    TEXT NOT NULL DEFAULT '',
    manual_input      INTEGER NOT NULL DEFAULT 0,
    content           TEXT,
    content_summary   TEXT NOT NULL DEFAULT '',
    category          TEXT NOT NULL DEFAULT 'general',
    entities_json     TEXT NOT NULL DEFAULT '[]',
    as_of             TEXT NOT NULL,
    confidence        REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'accepted', 'rejected', 'expired', 'quarantined')),
    policy_codes_json TEXT NOT NULL DEFAULT '[]',
    reason            TEXT NOT NULL DEFAULT '',
    accepted_fact_id  INTEGER REFERENCES facts(fact_id),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    expires_at        TEXT NOT NULL
    ,UNIQUE(principal_id, submission_uid)
);

CREATE INDEX IF NOT EXISTS idx_candidates_status_expiry
    ON candidates(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_candidates_principal
    ON candidates(principal_id, status);

CREATE TABLE IF NOT EXISTS supersessions (
    supersession_uid TEXT PRIMARY KEY,
    old_fact_id      INTEGER NOT NULL UNIQUE REFERENCES facts(fact_id) ON DELETE CASCADE,
    new_fact_id      INTEGER NOT NULL REFERENCES facts(fact_id),
    reason           TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    CHECK (old_fact_id <> new_fact_id)
);

CREATE TABLE IF NOT EXISTS audit (
    audit_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    action     TEXT NOT NULL,
    fact_id    INTEGER,
    actor      TEXT NOT NULL DEFAULT 'cli',
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    event_uid      TEXT PRIMARY KEY,
    operation_uid  TEXT NOT NULL,
    principal_uid  TEXT NOT NULL DEFAULT '',
    action         TEXT NOT NULL,
    object_type    TEXT NOT NULL,
    object_uid     TEXT NOT NULL DEFAULT '',
    outcome        TEXT NOT NULL DEFAULT 'success',
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_events_object
    ON audit_events(object_type, object_uid, created_at);

CREATE TABLE IF NOT EXISTS change_events (
    sequence       INTEGER PRIMARY KEY AUTOINCREMENT,
    event_uid      TEXT NOT NULL UNIQUE,
    library_uid    TEXT NOT NULL REFERENCES libraries(library_uid),
    device_uid     TEXT NOT NULL REFERENCES devices(device_uid),
    operation_uid  TEXT NOT NULL,
    object_type    TEXT NOT NULL,
    object_uid     TEXT NOT NULL,
    action         TEXT NOT NULL,
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS migration_issues (
    issue_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_code  TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id   TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projection_state (
    projection_key TEXT PRIMARY KEY,
    status         TEXT NOT NULL CHECK (status IN ('clean', 'dirty', 'disabled')),
    operation_uid  TEXT NOT NULL DEFAULT '',
    error_code     TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purge_tombstones (
    tombstone_uid TEXT PRIMARY KEY,
    fact_uid      TEXT NOT NULL,
    purged_at     TEXT NOT NULL,
    reason_code   TEXT NOT NULL DEFAULT 'user_requested'
);

CREATE TABLE IF NOT EXISTS fact_search_terms (
    fact_id INTEGER NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    term    TEXT NOT NULL,
    kind    TEXT NOT NULL CHECK (kind IN ('latin', 'cjk1', 'cjk2')),
    PRIMARY KEY (fact_id, term, kind)
);

CREATE INDEX IF NOT EXISTS idx_fact_search_terms_term ON fact_search_terms(term, kind);
CREATE INDEX IF NOT EXISTS idx_fact_search_terms_lookup ON fact_search_terms(term, kind, fact_id);

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

CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE OF tags ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags)
        VALUES ('delete', old.fact_id, old.content, old.tags);
    INSERT INTO facts_fts(rowid, content, tags)
        VALUES (new.fact_id, new.content, new.tags);
END;
"""
