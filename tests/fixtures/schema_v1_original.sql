-- Original v1 SCHEMA from f2a6a6e47b904234711944335065402235ab7fdf.

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
