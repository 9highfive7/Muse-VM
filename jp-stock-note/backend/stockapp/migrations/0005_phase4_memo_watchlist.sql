-- 0005_phase4_memo_watchlist: 構造化判断メモ（リビジョン＋スナップショット）、
-- 自己評価の追記、ウォッチリスト (要件 3.9 / 3.10 / 7.3).
--
-- 「改変不可」(要件 7.3) = the app cannot change or delete these rows (the
-- triggers below RAISE on UPDATE and DELETE) and every row carries a SHA-256
-- of its canonical content, so a change made outside the app (e.g. by
-- editing the SQLite file with the triggers dropped) is detected by
-- stockapp.memos.verify_*. Editing the local file directly cannot be
-- prevented; S5 states this.

-- One row per memo (= one judgment record for one stock). Append-only.
CREATE TABLE memo (
    memo_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,             -- 5-digit code
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_memo_code ON memo(code, memo_id);

-- Each save (the first one and every later edit) is a new revision.
-- content_json = the user's own entries (stance, evidence checklist,
-- scenario, review conditions, review date) as canonical JSON.
CREATE TABLE memo_revision (
    revision_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    memo_id         INTEGER NOT NULL REFERENCES memo(memo_id),
    revision_no     INTEGER NOT NULL,      -- 1, 2, ... within the memo
    content_json    TEXT NOT NULL,
    review_date     TEXT,                  -- copy of content.review_date for the S1 "due" query
    created_at      TEXT NOT NULL,
    content_sha256  TEXT NOT NULL,
    UNIQUE (memo_id, revision_no)
);
CREATE INDEX idx_memo_revision_review_date ON memo_revision(review_date);

-- The values shown on the stock page at save time (header price, stage
-- facts, card sentences, valuation, macro headline values), one per revision.
-- values_json is canonical JSON; sha256 covers memo/revision/code/time + values.
CREATE TABLE memo_snapshot (
    snapshot_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_id   INTEGER NOT NULL UNIQUE REFERENCES memo_revision(revision_id),
    memo_id       INTEGER NOT NULL REFERENCES memo(memo_id),
    code          TEXT NOT NULL,
    captured_at   TEXT NOT NULL,
    values_json   TEXT NOT NULL,
    sha256        TEXT NOT NULL
);

-- 自己評価（プロセス × 結果）: a later annotation on a revision. Never
-- overwrites the revision or its snapshot; also append-only.
CREATE TABLE memo_review (
    review_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    memo_id         INTEGER NOT NULL REFERENCES memo(memo_id),
    revision_id     INTEGER NOT NULL REFERENCES memo_revision(revision_id),
    content_json    TEXT NOT NULL,         -- process / result self-rating + note
    created_at      TEXT NOT NULL,
    content_sha256  TEXT NOT NULL
);
CREATE INDEX idx_memo_review_memo ON memo_review(memo_id, review_id);

CREATE TRIGGER memo_no_update BEFORE UPDATE ON memo
BEGIN SELECT RAISE(ABORT, 'memo is append-only: UPDATE is not allowed'); END;
CREATE TRIGGER memo_no_delete BEFORE DELETE ON memo
BEGIN SELECT RAISE(ABORT, 'memo is append-only: DELETE is not allowed'); END;

CREATE TRIGGER memo_revision_no_update BEFORE UPDATE ON memo_revision
BEGIN SELECT RAISE(ABORT, 'memo_revision is append-only: UPDATE is not allowed'); END;
CREATE TRIGGER memo_revision_no_delete BEFORE DELETE ON memo_revision
BEGIN SELECT RAISE(ABORT, 'memo_revision is append-only: DELETE is not allowed'); END;

CREATE TRIGGER memo_snapshot_no_update BEFORE UPDATE ON memo_snapshot
BEGIN SELECT RAISE(ABORT, 'memo_snapshot is immutable: UPDATE is not allowed'); END;
CREATE TRIGGER memo_snapshot_no_delete BEFORE DELETE ON memo_snapshot
BEGIN SELECT RAISE(ABORT, 'memo_snapshot is immutable: DELETE is not allowed'); END;

CREATE TRIGGER memo_review_no_update BEFORE UPDATE ON memo_review
BEGIN SELECT RAISE(ABORT, 'memo_review is append-only: UPDATE is not allowed'); END;
CREATE TRIGGER memo_review_no_delete BEFORE DELETE ON memo_review
BEGIN SELECT RAISE(ABORT, 'memo_review is append-only: DELETE is not allowed'); END;

-- ウォッチリスト (要件 3.9 / 7.3). Removing sets removed_at (history kept);
-- at most one active row per code.
CREATE TABLE watchlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,
    reason      TEXT NOT NULL,             -- 監視理由（自由記述, required, non-empty）
    tags_json   TEXT NOT NULL DEFAULT '[]',-- 監視理由タグ (JSON array of preset labels)
    added_at    TEXT NOT NULL,
    removed_at  TEXT
);
CREATE UNIQUE INDEX idx_watchlist_active ON watchlist(code) WHERE removed_at IS NULL;
CREATE TRIGGER watchlist_reason_not_empty BEFORE INSERT ON watchlist
WHEN trim(NEW.reason) = ''
BEGIN SELECT RAISE(ABORT, 'watchlist.reason must not be empty'); END;
