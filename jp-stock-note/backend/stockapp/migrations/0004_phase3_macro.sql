-- 0004_phase3_macro: FRED observations + per-series meta, app settings.

-- FRED各系列 (fredgraph.csv, 要件 4.1)  PK = 系列ID + 観測日.
-- value IS NULL = FRED reported the observation as missing ('.' or empty);
-- kept as missing, never 0 (要件 4.1 備考).
CREATE TABLE fred_observations (
    series_id   TEXT NOT NULL,
    date        TEXT NOT NULL,          -- observation date (YYYY-MM-DD; monthly = first of month)
    value       REAL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (series_id, date)
);

-- Per-series description (要件 4.5): frequency, source and the expected lag
-- as found in research-03. Fetch status / as-of live in series_meta
-- (source='fred'), like the J-Quants jobs.
CREATE TABLE fred_series (
    series_id         TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    frequency         TEXT NOT NULL,     -- 'daily' | 'monthly' (observation frequency)
    fetch_cycle       TEXT NOT NULL,     -- 'daily' | 'weekly' (how often we fetch)
    source            TEXT NOT NULL,     -- e.g. 'FRED (Board of Governors)' / 'FRED (OECD)'
    source_url        TEXT NOT NULL,
    lag_note          TEXT NOT NULL,     -- expected lag description
    cadence_badge     TEXT NOT NULL,
    last_change_at    TEXT,              -- last fetch that delivered a new/changed observation
    last_unchanged_at TEXT,              -- last fetch with no new observation (要件 4.5 ドル円)
    updated_at        TEXT NOT NULL
);

-- Editable app settings (key -> JSON). 短観想定レート (要件 3.8 / 9.1-8).
CREATE TABLE app_settings (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

INSERT INTO app_settings(key, value_json, updated_at) VALUES (
    'tankan_usdjpy',
    '{"value": 154.23, "survey_label": "日銀短観 2026年9月調査・全産業", "origin": "default"}',
    strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
);
