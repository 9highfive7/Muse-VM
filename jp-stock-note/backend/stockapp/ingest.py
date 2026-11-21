"""Fetch jobs: J-Quants -> SQLite, with fetch_log / api_request_log / series_meta
bookkeeping. Phase 0 implements the stock master and the trading calendar.

CLI:  python -m stockapp.ingest master|calendar [--from YYYY-MM-DD --to YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from .db import utc_now
from .jquants.client import (
    ENDPOINT_CALENDAR,
    ENDPOINT_DAILY_BARS,
    ENDPOINT_FINS_SUMMARY,
    ENDPOINT_MASTER,
    ENDPOINT_TOPIX,
    FetchResult,
    JQuantsClient,
    JQuantsError,
)
from .jquants.ratelimit import Attempt, Priority
from .normalize import normalize_text

JOBS = {
    "stock_master": {"endpoint": ENDPOINT_MASTER, "update_cycle": "weekly"},
    "trading_calendar": {"endpoint": ENDPOINT_CALENDAR, "update_cycle": "monthly"},
    # Phase 1 (implemented in stockapp.bars)
    "daily_bars_by_date": {"endpoint": ENDPOINT_DAILY_BARS, "update_cycle": "daily"},
    "daily_bars_by_code": {"endpoint": ENDPOINT_DAILY_BARS, "update_cycle": "on_demand"},
    "topix_bars": {"endpoint": ENDPOINT_TOPIX, "update_cycle": "daily"},
    # Phase 2 (implemented in stockapp.fins)
    "fins_summary_by_code": {"endpoint": ENDPOINT_FINS_SUMMARY, "update_cycle": "on_demand"},
    "fins_summary_by_date": {"endpoint": ENDPOINT_FINS_SUMMARY, "update_cycle": "daily"},
    # Phase 3 (implemented in stockapp.fred): one fetch_log row per series;
    # series_meta is keyed (source='fred', series_id=<FRED id>).
    "fred_series": {"endpoint": "/graph/fredgraph.csv", "update_cycle": "daily"},
}

# HolDiv: 0 = 非営業日, 1 = 営業日, 2 = 東証半日立会日, 3 = 非営業日(祝日取引あり)
BUSINESS_HOLDIV = {"1", "2"}


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds")


def _start(conn: sqlite3.Connection, job: str, params: dict, priority: int) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO fetch_log(job, endpoint, params_json, priority, started_at, result)"
            " VALUES (?, ?, ?, ?, ?, 'running')",
            (job, JOBS[job]["endpoint"], json.dumps(params, ensure_ascii=False), int(priority), utc_now()),
        )
        return int(cur.lastrowid)


def _log_attempts(conn: sqlite3.Connection, fetch_id: int, endpoint: str, attempts: list[Attempt]) -> None:
    conn.executemany(
        "INSERT INTO api_request_log(fetch_log_id, endpoint, sent_at, attempt, http_status, outcome, elapsed_ms)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(fetch_id, endpoint, _iso(a.sent_at), a.attempt, a.status, a.outcome, a.elapsed_ms) for a in attempts],
    )


def _finish(
    conn: sqlite3.Connection,
    fetch_id: int,
    job: str,
    *,
    result: str,
    attempts: list[Attempt],
    status: int | None = None,
    reason: str | None = None,
    row_count: int | None = None,
    as_of: str | None = None,
    source: str = "jquants",
    series_id: str | None = None,
    update_cycle: str | None = None,
) -> None:
    now = utc_now()
    endpoint = JOBS[job]["endpoint"]
    series_id = series_id or job
    update_cycle = update_cycle or JOBS[job]["update_cycle"]
    _log_attempts(conn, fetch_id, endpoint, attempts)
    conn.execute(
        "UPDATE fetch_log SET finished_at=?, result=?, http_status=?, error_reason=?, attempts=?, row_count=?"
        " WHERE id=?",
        (now, result, status, reason, len(attempts), row_count, fetch_id),
    )
    conn.execute(
        "INSERT INTO series_meta(source, series_id, update_cycle, last_attempt_at, last_result, last_error)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(source, series_id) DO UPDATE SET update_cycle=excluded.update_cycle,"
        " last_attempt_at=excluded.last_attempt_at, last_result=excluded.last_result,"
        " last_error=excluded.last_error",
        (source, series_id, update_cycle, now, result, reason),
    )
    if result == "ok":
        conn.execute(
            "UPDATE series_meta SET last_success_at=?, as_of_date=COALESCE(?, as_of_date)"
            " WHERE source=? AND series_id=?",
            (now, as_of, source, series_id),
        )


def _run(
    conn: sqlite3.Connection,
    job: str,
    params: dict,
    priority: int,
    fetch: Callable[[], FetchResult],
    store: Callable[[list[dict[str, Any]], str], str | None],
) -> dict:
    fetch_id = _start(conn, job, params, priority)
    try:
        res = fetch()
    except JQuantsError as exc:
        with conn:
            _finish(conn, fetch_id, job, result=exc.outcome, attempts=exc.attempts, status=exc.status, reason=exc.reason)
        return {"fetch_id": fetch_id, "result": exc.outcome, "error_reason": exc.reason}
    except Exception as exc:  # unexpected: record, never include secrets
        with conn:
            _finish(conn, fetch_id, job, result="error", attempts=[], reason=f"内部エラー: {type(exc).__name__}")
        return {"fetch_id": fetch_id, "result": "error", "error_reason": type(exc).__name__}
    fetched_at = utc_now()
    with conn:  # rows + log commit atomically
        as_of = store(res.rows, fetched_at)
        _finish(conn, fetch_id, job, result="ok", attempts=res.attempts, status=200,
                row_count=len(res.rows), as_of=as_of)
    return {"fetch_id": fetch_id, "result": "ok", "row_count": len(res.rows)}


def _s(row: dict, key: str) -> str | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    return str(value)


def store_stock_master(conn: sqlite3.Connection, rows: list[dict[str, Any]], fetched_at: str) -> str | None:
    params = []
    for r in rows:
        code, day, name = _s(r, "Code"), _s(r, "Date"), _s(r, "CoName")
        if not (code and day and name):
            continue
        params.append((
            code, day, name, _s(r, "CoNameEn"),
            _s(r, "S17"), _s(r, "S17Nm"), _s(r, "S33"), _s(r, "S33Nm"),
            _s(r, "ScaleCat"), _s(r, "Mkt"), _s(r, "MktNm"), _s(r, "Mrgn"), _s(r, "MrgnNm"),
            normalize_text(name), normalize_text(_s(r, "CoNameEn")) or None,
            json.dumps(r, ensure_ascii=False, sort_keys=True), fetched_at,
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO stock_master(code, date, name, name_en, sector17_code, sector17_name,"
        " sector33_code, sector33_name, scale_category, market_code, market_name, margin_code, margin_name,"
        " name_norm, name_en_norm, raw_json, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        params,
    )
    return max((p[1] for p in params), default=None)


def store_trading_calendar(conn: sqlite3.Connection, rows: list[dict[str, Any]], fetched_at: str) -> str | None:
    params = []
    for r in rows:
        day, div = _s(r, "Date"), _s(r, "HolDiv")
        if not (day and div is not None):
            continue
        params.append((day, div, 1 if div in BUSINESS_HOLDIV else 0, fetched_at))
    conn.executemany(
        "INSERT OR REPLACE INTO trading_calendar(date, holiday_division, is_business_day, fetched_at)"
        " VALUES (?,?,?,?)",
        params,
    )
    return max((p[0] for p in params), default=None)


def fetch_stock_master(
    conn: sqlite3.Connection, client: JQuantsClient, date: str | None = None,
    priority: int = Priority.P4_BACKFILL,
) -> dict:
    params = {"date": date} if date else {}
    return _run(conn, "stock_master", params, priority,
                lambda: client.stock_master(date, priority),
                lambda rows, ts: store_stock_master(conn, rows, ts))


def fetch_trading_calendar(
    conn: sqlite3.Connection, client: JQuantsClient, date_from: str | None = None, date_to: str | None = None,
    priority: int = Priority.P4_BACKFILL,
) -> dict:
    if date_from is None and date_to is None:
        today = date.today()
        date_from = (today - timedelta(days=370)).isoformat()
        date_to = (today + timedelta(days=370)).isoformat()
    params = {"from": date_from, "to": date_to}
    return _run(conn, "trading_calendar", params, priority,
                lambda: client.trading_calendar(date_from, date_to, priority),
                lambda rows, ts: store_trading_calendar(conn, rows, ts))


def main() -> int:
    from .config import load_settings
    from .db import connect, migrate
    from .runtime import build_client

    parser = argparse.ArgumentParser(description="Fetch J-Quants data into data/app.db")
    parser.add_argument("job", choices=["master", "calendar"])
    parser.add_argument("--date")
    parser.add_argument("--from", dest="date_from")
    parser.add_argument("--to", dest="date_to")
    args = parser.parse_args()

    settings = load_settings()
    conn = connect(settings.db_path)
    migrate(conn)
    client = build_client(settings)
    if args.job == "master":
        out = fetch_stock_master(conn, client, args.date)
    else:
        out = fetch_trading_calendar(conn, client, args.date_from, args.date_to)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["result"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
