-- 0002_phase1_bars: Phase 1 ingestion bookkeeping (daily bars / TOPIX).

CREATE INDEX IF NOT EXISTS idx_daily_bars_date ON daily_bars(date);

-- Per-series history state. series = 'bars:<code>' or 'topix'.
-- full_fetched_at is set when the full available history was fetched by code
-- (on-demand) or in full (TOPIX). Bars that arrive via the by-date batch do
-- not count as "full history".
CREATE TABLE history_state (
    series           TEXT PRIMARY KEY,
    full_fetched_at  TEXT,
    full_reason      TEXT,                 -- 'on_demand' | 'split' | 'initial'
    first_date       TEXT,
    last_date        TEXT,
    row_count        INTEGER
);

-- By-date sync results (nightly batch / startup catch-up).
-- A date counts as "successfully fetched" when result='ok' and row_count > 0.
CREATE TABLE daily_sync (
    date          TEXT PRIMARY KEY,
    result        TEXT NOT NULL,           -- ok | empty | <fetch error outcome>
    row_count     INTEGER,
    fetch_log_id  INTEGER REFERENCES fetch_log(id),
    updated_at    TEXT NOT NULL
);

-- Persistent fetch queue (survives restarts). Processed highest priority first
-- (P1 < P2 < P3 < P4), then FIFO. Requests themselves still go through the
-- in-process RequestQueue/token bucket.
CREATE TABLE fetch_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job           TEXT NOT NULL,           -- bars_by_date | bars_full | topix_full | topix_range
    code          TEXT,
    target_date   TEXT,
    params_json   TEXT,
    priority      INTEGER NOT NULL,
    reason        TEXT,                    -- catch_up | nightly | split_detected | on_demand
    status        TEXT NOT NULL DEFAULT 'pending',   -- pending | running | done | failed
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    result        TEXT,
    error_reason  TEXT
);
-- At most one open (pending/running) job per (job, code, date).
CREATE UNIQUE INDEX idx_fetch_queue_open
    ON fetch_queue(job, COALESCE(code, ''), COALESCE(target_date, ''))
    WHERE status IN ('pending', 'running');
CREATE INDEX idx_fetch_queue_status ON fetch_queue(status, priority, id);

-- Split / adjustment events seen in fetched bars (AdjFactor != 1).
CREATE TABLE adjustment_events (
    code          TEXT NOT NULL,
    date          TEXT NOT NULL,
    adj_factor    REAL NOT NULL,
    detected_at   TEXT NOT NULL,
    action        TEXT NOT NULL,           -- 'refetch_queued' | 'already_in_range' | 'no_prior_bars'
    PRIMARY KEY (code, date)
);
