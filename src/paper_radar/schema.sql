PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS journals (
    id TEXT NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    publisher TEXT NOT NULL CHECK (publisher IN ('nature', 'aip', 'ieee', 'wiley')),
    feed_url TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    etag TEXT,
    last_modified TEXT,
    last_checked_at TEXT,
    last_success_at TEXT,
    last_status TEXT NOT NULL DEFAULT 'never',
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS articles (
    uid TEXT NOT NULL PRIMARY KEY,
    doi TEXT,
    journal_id TEXT REFERENCES journals(id),
    title TEXT NOT NULL,
    abstract TEXT,
    authors_json TEXT NOT NULL DEFAULT '[]',
    published_at TEXT,
    article_type TEXT NOT NULL DEFAULT 'other'
        CHECK (article_type IN ('research', 'review', 'editorial', 'correction', 'other')),
    article_url TEXT NOT NULL,
    normalized_url TEXT,
    oa_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (oa_status IN ('open', 'closed', 'unknown')),
    source_feed_url TEXT NOT NULL,
    metadata_status TEXT NOT NULL DEFAULT 'rss_only'
        CHECK (metadata_status IN ('rss_only', 'enriched', 'partial')),
    first_seen_at TEXT NOT NULL,
    last_updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_doi_unique
    ON articles(doi)
    WHERE doi IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_normalized_url_unique
    ON articles(normalized_url)
    WHERE normalized_url IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_articles_published_at
    ON articles(published_at DESC);

CREATE INDEX IF NOT EXISTS idx_articles_journal_id
    ON articles(journal_id);

CREATE INDEX IF NOT EXISTS idx_articles_article_type
    ON articles(article_type);

CREATE INDEX IF NOT EXISTS idx_articles_oa_status
    ON articles(oa_status);

CREATE TABLE IF NOT EXISTS tags (
    id TEXT NOT NULL PRIMARY KEY,
    label TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS article_tags (
    article_uid TEXT NOT NULL REFERENCES articles(uid) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (article_uid, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_article_tags_tag_id_article_uid
    ON article_tags(tag_id, article_uid);

CREATE TABLE IF NOT EXISTS runs_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'partial', 'error')),
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
);

PRAGMA user_version = 1;
