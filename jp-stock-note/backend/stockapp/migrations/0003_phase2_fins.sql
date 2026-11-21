-- 0003_phase2_fins: Phase 2 financial summary columns + correction log.
-- (Numbered 0003 because 0002 is already the Phase 1 bars migration.)
-- Field mapping: docs/api-field-mapping.md (決算サマリー). Values that the API
-- delivers as "" are stored as NULL, never as 0.

-- Current-fiscal-year forecasts (kQ statements, forecast-revision disclosures)
ALTER TABLE fins_summary ADD COLUMN forecast_ordinary_profit REAL;     -- FOdP
ALTER TABLE fins_summary ADD COLUMN forecast_profit REAL;              -- FNP
ALTER TABLE fins_summary ADD COLUMN forecast_eps REAL;                 -- FEPS
ALTER TABLE fins_summary ADD COLUMN forecast_dividend_annual REAL;     -- FDivAnn
-- Actual dividend (FY statements)
ALTER TABLE fins_summary ADD COLUMN dividend_annual REAL;              -- DivAnn
-- Next-fiscal-year forecasts (FY statements: the 期初予想 of the next year)
ALTER TABLE fins_summary ADD COLUMN next_fy_start TEXT;                -- NxtFYSt
ALTER TABLE fins_summary ADD COLUMN next_fy_end TEXT;                  -- NxtFYEn
ALTER TABLE fins_summary ADD COLUMN next_forecast_net_sales REAL;      -- NxFSales
ALTER TABLE fins_summary ADD COLUMN next_forecast_operating_profit REAL; -- NxFOP
ALTER TABLE fins_summary ADD COLUMN next_forecast_ordinary_profit REAL; -- NxFOdP
ALTER TABLE fins_summary ADD COLUMN next_forecast_profit REAL;         -- NxFNp / NxFNP
ALTER TABLE fins_summary ADD COLUMN next_forecast_eps REAL;            -- NxFEPS
ALTER TABLE fins_summary ADD COLUMN next_forecast_dividend_annual REAL; -- NxFDivAnn
-- Parsed from DocType (used for the quarterly-standalone conditions, 要件 5.3)
ALTER TABLE fins_summary ADD COLUMN doc_kind TEXT;            -- statement | forecast_revision | dividend_revision | other
ALTER TABLE fins_summary ADD COLUMN consolidation TEXT;       -- consolidated | non_consolidated | NULL
ALTER TABLE fins_summary ADD COLUMN accounting_standard TEXT; -- JP | IFRS | US | JMIS | NULL
ALTER TABLE fins_summary ADD COLUMN first_fetched_at TEXT;
ALTER TABLE fins_summary ADD COLUMN updated_at TEXT;

CREATE INDEX IF NOT EXISTS idx_fins_summary_code_fy ON fins_summary(code, fy_end, period_type);

-- 訂正検知ログ (要件 4.6): one row per detected content change of a stored
-- disclosure_no. The stored row is overwritten with the new values; the old
-- values are kept here (old_raw_json = full previous response row).
CREATE TABLE fins_revision_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    disclosure_no    TEXT NOT NULL,
    code             TEXT NOT NULL,
    detected_at      TEXT NOT NULL,
    changes_json     TEXT NOT NULL,     -- [{"field": API field, "column": logical name|null, "old": .., "new": ..}]
    old_sha256       TEXT NOT NULL,
    new_sha256       TEXT NOT NULL,
    old_raw_json     TEXT NOT NULL,
    old_fetched_at   TEXT
);
CREATE INDEX idx_fins_revision_log_code ON fins_revision_log(code, detected_at);
