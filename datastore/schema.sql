-- gxx local activity store (SQLite sidecar to the turbovec embedding index).
-- One row per stored observation; the same integer `id` keys the turbovec vector.

CREATE TABLE IF NOT EXISTS activity (
    id            INTEGER PRIMARY KEY,   -- monotonic; also the turbovec uint64 id
    ts            INTEGER NOT NULL,      -- unix epoch seconds (UTC)
    app           TEXT,                  -- foreground app / process name
    window        TEXT,                  -- window title
    summary       TEXT NOT NULL,         -- distilled, embeddable description
    salient_text  TEXT,                  -- key on-screen text (from UIA or vision)
    transcription TEXT,                  -- exact-ish transcription for keyword lookup
    entities_json TEXT,                  -- JSON array of notable entities
    tags          TEXT,                  -- comma-separated tags
    content_hash  INTEGER NOT NULL,      -- exact hash of the text content (dedup key)
    simhash       INTEGER,               -- word-shingle simhash (fuzzy near-dup, optional)
    trigger       TEXT,                  -- what triggered this capture (AppSwitch, VisualChange, ...)
    source        TEXT,                  -- 'uia' | 'vision' | 'manual'
    is_actionable INTEGER DEFAULT 0,     -- gate hint from the understanding layer
    embedded      INTEGER DEFAULT 0,     -- 1 once a vector exists in turbovec
    evicted_at    INTEGER                -- set when any cached blob is evicted (row kept)
);

CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity (ts);
CREATE INDEX IF NOT EXISTS idx_activity_content_hash ON activity (content_hash);
CREATE INDEX IF NOT EXISTS idx_activity_app ON activity (app);

-- Single-row bookkeeping (id high-water mark, schema version, last optimize ts).
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value INTEGER
);
