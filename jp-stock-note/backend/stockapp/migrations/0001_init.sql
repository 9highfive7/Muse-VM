-- 0001_init: Phase 0 skeleton schema.
-- Column names are the app's logical names; see docs/api-field-mapping.md
-- for the J-Quants v2 field each column comes from.

-- 銘柄マスタ  (/v2/equities/master)  PK = code + effective date
CREATE TABLE stock_master (
    code            TEXT NOT NULL,          -- 5-digit J-Quants code, e.g. 72030
    date            TEXT NOT NULL,          -- 適用日 (YYYY-MM-DD)
    name            TEXT NOT NULL,
    name_en         TEXT,
    sector17_code   TEXT,
    sector17_name   TEXT,
    sector33_code   TEXT,
    sector33_name   TEXT,
    scale_category  TEXT,
    market_code     TEXT,
    market_name     TEXT,
    margin_code     TEXT,
    margin_name     TEXT,
    name_norm       TEXT NOT NULL,          -- NFKC + casefold, for search
    name_en_norm    TEXT,
    raw_json        TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (code, date)
);
CREATE INDEX idx_stock_master_name_norm ON stock_master(name_norm);

-- 営業日カレンダー  (/v2/markets/calendar)
CREATE TABLE trading_calendar (
    date             TEXT PRIMARY KEY,
    holiday_division TEXT NOT NULL,        -- HolDiv as delivered
    is_business_day  INTEGER NOT NULL,     -- 1 when HolDiv in ('1','2')
    fetched_at       TEXT NOT NULL
);

-- 日足  (/v2/equities/bars/daily)  raw + adjusted
CREATE TABLE daily_bars (
    code          TEXT NOT NULL,
    date          TEXT NOT NULL,
    open          REAL,
    high          REAL,
    low           REAL,
    close         REAL,
    upper_limit   TEXT,                    -- UL flag ('0'/'1')
    lower_limit   TEXT,                    -- LL flag ('0'/'1')
    volume        REAL,
    turnover      REAL,
    adj_factor    REAL,
    adj_open      REAL,
    adj_high      REAL,
    adj_low       REAL,
    adj_close     REAL,
    adj_volume    REAL,
    market_cap    REAL,
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (code, date)
);

-- TOPIX日足  (/v2/indices/bars/daily/topix)
CREATE TABLE topix_bars (
    date        TEXT PRIMARY KEY,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    fetched_at  TEXT NOT NULL
);

-- 決算サマリー  (/v2/fins/summary)  PK = 開示番号; raw JSON kept verbatim
CREATE TABLE fins_summary (
    disclosure_no        TEXT PRIMARY KEY,
    code                 TEXT NOT NULL,
    disclosed_date       TEXT,
    disclosed_time       TEXT,
    doc_type             TEXT,
    period_type          TEXT,
    period_start         TEXT,
    period_end           TEXT,
    fy_start             TEXT,
    fy_end               TEXT,
    net_sales            REAL,
    operating_profit     REAL,
    ordinary_profit      REAL,
    profit               REAL,
    eps                  REAL,
    total_assets         REAL,
    equity               REAL,
    equity_ratio         REAL,
    bps                  REAL,
    cf_operating         REAL,
    cf_investing         REAL,
    cf_financing         REAL,
    cash_equivalents     REAL,
    forecast_net_sales   REAL,
    forecast_operating_profit REAL,
    raw_json             TEXT NOT NULL,
    content_sha256       TEXT NOT NULL,
    fetched_at           TEXT NOT NULL
);
CREATE INDEX idx_fins_summary_code_date ON fins_summary(code, disclosed_date);

-- 取得履歴: one row per logical fetch job
CREATE TABLE fetch_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job            TEXT NOT NULL,          -- e.g. 'stock_master', 'trading_calendar'
    endpoint       TEXT NOT NULL,
    params_json    TEXT,
    priority       INTEGER,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    result         TEXT NOT NULL DEFAULT 'running',
                   -- running | ok | plan_limit | rate_limited | server_error
                   -- | timeout | auth_error | no_api_key | http_error | error
    http_status    INTEGER,
    error_reason   TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    row_count      INTEGER
);
CREATE INDEX idx_fetch_log_started ON fetch_log(started_at);

-- 送出ログ: one row per HTTP request actually sent (rate-limit audit)
CREATE TABLE api_request_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_log_id  INTEGER REFERENCES fetch_log(id),
    endpoint      TEXT NOT NULL,
    sent_at       TEXT NOT NULL,
    attempt       INTEGER NOT NULL,
    http_status   INTEGER,
    outcome       TEXT NOT NULL,
    elapsed_ms    INTEGER
);
CREATE INDEX idx_api_request_log_sent ON api_request_log(sent_at);

-- 系列メタ: last success / as-of per source+series (drives status badges)
CREATE TABLE series_meta (
    source           TEXT NOT NULL,        -- 'jquants' | 'fred'
    series_id        TEXT NOT NULL,        -- e.g. 'stock_master', 'DGS10'
    update_cycle     TEXT,                 -- 'daily' | 'weekly' | 'monthly'
    as_of_date       TEXT,                 -- latest observation date held
    last_attempt_at  TEXT,
    last_success_at  TEXT,
    last_result      TEXT,
    last_error       TEXT,
    PRIMARY KEY (source, series_id)
);
