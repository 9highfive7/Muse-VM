"""Phase 1 ingestion: daily bars (by code / by date), TOPIX, split handling,
the persistent fetch queue and the startup catch-up (要件 4.2〜4.4).

* On-demand (P1): ``ensure_history`` — when a stock page opens and that code's
  full history has never been fetched by code, fetch it (and TOPIX if needed).
  If it has, only a short tail fetch is made when the stored latest bar is
  older than the latest available business day; otherwise no API call.
* Nightly / catch-up (P2 / P4): ``queue_catchup`` finds business days (from
  ``trading_calendar``) after the last successful by-date fetch that are not
  yet stored, and queues one by-date job per day plus one TOPIX range job.
* Split handling (P3): when a by-date (or tail) fetch delivers a bar whose
  ``AdjFactor`` != 1 for a date after that code's stored range, the stored
  adjusted series is stale, so the code's full history is re-fetched.

Every HTTP request still goes through the shared RequestQueue/token bucket via
``JQuantsClient``; this module only decides *what* to fetch.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .db import utc_now
from .ingest import _run, _s
from .jquants.client import JQuantsClient
from .jquants.ratelimit import Priority
from .queries import resolve_code

log = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))
# Provisional (要件 4.2 / 9.1-1): the day's bars are treated as available from
# 20:00 JST. Before that, "today" is not yet an expected business day.
PUBLISH_CUTOFF_HOUR_JST = 20
TAIL_CHECK_TTL_SECONDS = 30 * 60
_EPS = 1e-12

_code_locks: dict[str, threading.Lock] = {}
_code_locks_guard = threading.Lock()
_tail_checked: dict[str, float] = {}


def now_jst() -> datetime:
    return datetime.now(JST)


def _next_day(d: str) -> str:
    return (date.fromisoformat(d) + timedelta(days=1)).isoformat()


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flag(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(int(float(value))) if isinstance(value, (int, float)) else str(value)


def _is_adjustment(factor: float | None) -> bool:
    return factor is not None and abs(factor - 1.0) > _EPS


# --------------------------------------------------------------------------- storage

_BAR_COLUMNS = (
    "code, date, open, high, low, close, upper_limit, lower_limit, volume, turnover, adj_factor,"
    " adj_open, adj_high, adj_low, adj_close, adj_volume, market_cap, fetched_at"
)


def _bar_params(rows: list[dict[str, Any]], fetched_at: str) -> list[tuple]:
    params = []
    for r in rows:
        code, day = _s(r, "Code"), _s(r, "Date")
        if not (code and day):
            continue
        params.append((
            code, day, _num(r.get("O")), _num(r.get("H")), _num(r.get("L")), _num(r.get("C")),
            _flag(r.get("UL")), _flag(r.get("LL")), _num(r.get("Vo")), _num(r.get("Va")),
            _num(r.get("AdjFactor")), _num(r.get("AdjO")), _num(r.get("AdjH")), _num(r.get("AdjL")),
            _num(r.get("AdjC")), _num(r.get("AdjVo")), _num(r.get("MktCap")), fetched_at,
        ))
    return params


def store_daily_bars(conn: sqlite3.Connection, rows: list[dict[str, Any]], fetched_at: str) -> tuple[int, str | None]:
    params = _bar_params(rows, fetched_at)
    conn.executemany(
        f"INSERT OR REPLACE INTO daily_bars({_BAR_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", params
    )
    return len(params), max((p[1] for p in params), default=None)


def store_topix_bars(conn: sqlite3.Connection, rows: list[dict[str, Any]], fetched_at: str) -> tuple[int, str | None]:
    params = []
    for r in rows:
        day = _s(r, "Date")
        if not day:
            continue
        params.append((day, _num(r.get("O")), _num(r.get("H")), _num(r.get("L")), _num(r.get("C")), fetched_at))
    conn.executemany(
        "INSERT OR REPLACE INTO topix_bars(date, open, high, low, close, fetched_at) VALUES (?,?,?,?,?,?)", params
    )
    return len(params), max((p[0] for p in params), default=None)


def _update_history_state(conn: sqlite3.Connection, series: str, *, full_reason: str | None) -> None:
    if series == "topix":
        row = conn.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM topix_bars").fetchone()
    else:
        row = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM daily_bars WHERE code = ?", (series.split(":", 1)[1],)
        ).fetchone()
    first, last, count = row[0], row[1], row[2]
    if full_reason:
        conn.execute(
            "INSERT INTO history_state(series, full_fetched_at, full_reason, first_date, last_date, row_count)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT(series) DO UPDATE SET full_fetched_at=excluded.full_fetched_at,"
            " full_reason=excluded.full_reason, first_date=excluded.first_date, last_date=excluded.last_date,"
            " row_count=excluded.row_count",
            (series, utc_now(), full_reason, first, last, count),
        )
    else:
        conn.execute(
            "INSERT INTO history_state(series, first_date, last_date, row_count) VALUES (?,?,?,?)"
            " ON CONFLICT(series) DO UPDATE SET first_date=excluded.first_date, last_date=excluded.last_date,"
            " row_count=excluded.row_count",
            (series, first, last, count),
        )


def history_state(conn: sqlite3.Connection, series: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM history_state WHERE series = ?", (series,)).fetchone()


def _detect_adjustments(conn: sqlite3.Connection, rows: list[dict[str, Any]], *, queue: bool) -> list[str]:
    """Call BEFORE storing ``rows``. Returns codes whose stored adjusted series is
    now stale (AdjFactor != 1 on a date after the stored range). With
    ``queue=True`` a P3 full re-fetch is queued for each."""
    stale: list[str] = []
    now = utc_now()
    for r in rows:
        factor = _num(r.get("AdjFactor"))
        code, day = _s(r, "Code"), _s(r, "Date")
        if not (code and day) or not _is_adjustment(factor):
            continue
        prior_max = conn.execute("SELECT MAX(date) FROM daily_bars WHERE code = ?", (code,)).fetchone()[0]
        if prior_max is None:
            action = "no_prior_bars"
        elif prior_max < day:
            action = "refetch_queued" if queue else "refetch_inline"
            if code not in stale:
                stale.append(code)
            if queue:
                enqueue(conn, "bars_full", Priority.P3_REFETCH, code=code, reason="split_detected",
                        params={"adj_factor": factor, "date": day})
        else:
            action = "already_in_range"
        conn.execute(
            "INSERT OR IGNORE INTO adjustment_events(code, date, adj_factor, detected_at, action) VALUES (?,?,?,?,?)",
            (code, day, factor, now, action),
        )
    return stale


def _rescale_older_rows(conn: sqlite3.Connection, code: str, rows: list[dict[str, Any]]) -> int:
    """Call BEFORE storing a full re-fetch. Rows older than the API's history
    window are kept (要件 4.4: never delete) but would sit on the old
    adjustment basis; rescale them by new/old AdjC at the earliest overlapping
    date so the stored adjusted series stays continuous."""
    fetched = {(_s(r, "Date")): _num(r.get("AdjC")) for r in rows if _s(r, "Date")}
    if not fetched:
        return 0
    first_new = min(fetched)
    older = conn.execute("SELECT COUNT(*) FROM daily_bars WHERE code = ? AND date < ?", (code, first_new)).fetchone()[0]
    if not older:
        return 0
    for day in sorted(fetched):
        new_adj = fetched[day]
        old = conn.execute("SELECT adj_close FROM daily_bars WHERE code = ? AND date = ?", (code, day)).fetchone()
        if old and old[0] and new_adj:
            ratio = new_adj / old[0]
            if abs(ratio - 1.0) <= 1e-9:
                return 0
            conn.execute(
                "UPDATE daily_bars SET adj_open = adj_open * :r, adj_high = adj_high * :r, adj_low = adj_low * :r,"
                " adj_close = adj_close * :r, adj_volume = adj_volume / :r WHERE code = :c AND date < :d",
                {"r": ratio, "c": code, "d": first_new},
            )
            return older
    return 0


# --------------------------------------------------------------------------- fetch jobs

def fetch_bars_by_date(
    conn: sqlite3.Connection, client: JQuantsClient, day: str, priority: int = Priority.P2_NIGHTLY
) -> dict:
    """All codes for one business day (nightly batch / catch-up)."""
    info: dict[str, Any] = {"stored": 0, "split_refetch_queued": []}

    def store(rows, fetched_at):
        info["split_refetch_queued"] = _detect_adjustments(conn, rows, queue=True)
        info["stored"], _ = store_daily_bars(conn, rows, fetched_at)
        return day if info["stored"] else None

    out = _run(conn, "daily_bars_by_date", {"date": day}, priority,
               lambda: client.daily_bars(date=day, priority=priority), store)
    result = out["result"]
    if result == "ok" and not info["stored"]:
        result = "empty"  # business day not published yet
    with conn:
        conn.execute(
            "INSERT INTO daily_sync(date, result, row_count, fetch_log_id, updated_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT(date) DO UPDATE SET result=excluded.result, row_count=excluded.row_count,"
            " fetch_log_id=excluded.fetch_log_id, updated_at=excluded.updated_at",
            (day, result, info["stored"], out["fetch_id"], utc_now()),
        )
    out.update(date=day, sync_result=result, **info)
    return out


def fetch_bars_full(
    conn: sqlite3.Connection, client: JQuantsClient, code: str,
    priority: int = Priority.P1_ON_DEMAND, reason: str = "on_demand",
) -> dict:
    """One code's full available history (re-fetch after a split, or first open)."""
    code = resolve_code(code)
    info: dict[str, Any] = {"stored": 0, "rescaled_older_rows": 0}

    def store(rows, fetched_at):
        rows = [r for r in rows if _s(r, "Code") in (None, code)]
        info["rescaled_older_rows"] = _rescale_older_rows(conn, code, rows)
        info["stored"], last = store_daily_bars(conn, rows, fetched_at)
        _update_history_state(conn, f"bars:{code}", full_reason=reason)
        return last

    out = _run(conn, "daily_bars_by_code", {"code": code, "mode": "full", "reason": reason}, priority,
               lambda: client.daily_bars(code=code, priority=priority), store)
    out.update(code=code, **info)
    return out


def fetch_bars_since(
    conn: sqlite3.Connection, client: JQuantsClient, code: str, date_from: str,
    priority: int = Priority.P1_ON_DEMAND,
) -> dict:
    """Short tail fetch for one code. A split inside the tail triggers an
    immediate full re-fetch (the user is waiting, so it is not queued)."""
    code = resolve_code(code)
    info: dict[str, Any] = {"stored": 0, "split_detected": False}

    def store(rows, fetched_at):
        if _detect_adjustments(conn, rows, queue=False):
            info["split_detected"] = True
        info["stored"], last = store_daily_bars(conn, rows, fetched_at)
        _update_history_state(conn, f"bars:{code}", full_reason=None)
        return last

    out = _run(conn, "daily_bars_by_code", {"code": code, "mode": "since", "from": date_from}, priority,
               lambda: client.daily_bars(code=code, date_from=date_from, priority=priority), store)
    out.update(code=code, **info)
    if out["result"] == "ok" and info["split_detected"]:
        out["full_refetch"] = fetch_bars_full(conn, client, code, priority, reason="split")
    return out


def fetch_topix(
    conn: sqlite3.Connection, client: JQuantsClient, date_from: str | None = None, date_to: str | None = None,
    priority: int = Priority.P2_NIGHTLY,
) -> dict:
    full = date_from is None and date_to is None
    info: dict[str, Any] = {"stored": 0}

    def store(rows, fetched_at):
        info["stored"], last = store_topix_bars(conn, rows, fetched_at)
        _update_history_state(conn, "topix", full_reason="initial" if full else None)
        return last

    params = {"from": date_from, "to": date_to} if not full else {"mode": "full"}
    out = _run(conn, "topix_bars", params, priority,
               lambda: client.topix_bars(date_from, date_to, priority), store)
    out.update(**info)
    return out


# --------------------------------------------------------------------------- calendar logic

def latest_available_business_day(conn: sqlite3.Connection, now: datetime | None = None) -> str | None:
    """Latest business day whose bars should be published by ``now`` (JST).
    None when the calendar does not cover today (cannot decide)."""
    now = (now or now_jst()).astimezone(JST)
    today = now.date().isoformat()
    covered = conn.execute("SELECT 1 FROM trading_calendar WHERE date >= ? LIMIT 1", (today,)).fetchone()
    if not covered:
        return None
    op = "<=" if now.hour >= PUBLISH_CUTOFF_HOUR_JST else "<"
    row = conn.execute(
        f"SELECT MAX(date) FROM trading_calendar WHERE is_business_day = 1 AND date {op} ?", (today,)
    ).fetchone()
    return row[0] if row and row[0] else None


def last_successful_sync(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(date) FROM daily_sync WHERE result = 'ok' AND row_count > 0").fetchone()
    return row[0] if row and row[0] else None


def plan_catchup(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    """Business days that should be stored by ``now`` but have no successful
    by-date fetch, from the first successful sync onward. Fresh install (no
    successful sync yet): only the latest available business day — older days
    are covered per code by the on-demand full-history fetch."""
    end = latest_available_business_day(conn, now)
    if end is None:
        return {"end": None, "last_success": None, "dates": [],
                "reason": "営業日カレンダーが本日分まで取得されていないため、未取得日を判定できません。"}
    last_ok = last_successful_sync(conn)
    if last_ok is None:
        return {"end": end, "last_success": None, "dates": [end], "reason": "初回（最終成功日なし）"}
    first_ok = conn.execute(
        "SELECT MIN(date) FROM daily_sync WHERE result = 'ok' AND row_count > 0"
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT c.date FROM trading_calendar c"
        " LEFT JOIN daily_sync s ON s.date = c.date AND s.result = 'ok' AND s.row_count > 0"
        " WHERE c.is_business_day = 1 AND c.date > ? AND c.date <= ? AND s.date IS NULL ORDER BY c.date",
        (first_ok, end),
    ).fetchall()
    return {"end": end, "last_success": last_ok, "dates": [r[0] for r in rows], "reason": None}


# --------------------------------------------------------------------------- queue

def enqueue(
    conn: sqlite3.Connection, job: str, priority: int, *, code: str | None = None, target_date: str | None = None,
    params: dict | None = None, reason: str | None = None,
) -> bool:
    """Returns False if the same job is already pending/running."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO fetch_queue(job, code, target_date, params_json, priority, reason, status, created_at)"
        " VALUES (?,?,?,?,?,?, 'pending', ?)",
        (job, code, target_date, json.dumps(params or {}, ensure_ascii=False), int(priority), reason, utc_now()),
    )
    return cur.rowcount > 0


def pending_jobs(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM fetch_queue WHERE status IN ('pending','running') ORDER BY priority, id")]


def queue_catchup(
    conn: sqlite3.Connection, now: datetime | None = None, priority: int = Priority.P4_BACKFILL,
    reason: str = "catch_up",
) -> dict:
    plan = plan_catchup(conn, now)
    queued = []
    with conn:
        for d in plan["dates"]:
            if enqueue(conn, "bars_by_date", priority, target_date=d, reason=reason):
                queued.append(d)
        topix_job = None
        if plan["end"] is not None:
            st = history_state(conn, "topix")
            if st is None or not st["full_fetched_at"]:
                topix_job = "topix_full"
                enqueue(conn, "topix_full", priority, reason=reason)
            else:
                last = conn.execute("SELECT MAX(date) FROM topix_bars").fetchone()[0]
                if last is None or last < plan["end"]:
                    topix_job = "topix_range"
                    enqueue(conn, "topix_range", priority, target_date=plan["end"], reason=reason,
                            params={"from": _next_day(last) if last else None, "to": plan["end"]})
    return {**plan, "queued": queued, "topix_job": topix_job}


def reset_stale_running(conn: sqlite3.Connection) -> int:
    """Jobs left 'running' by a crashed/stopped process go back to pending."""
    with conn:
        return conn.execute("UPDATE fetch_queue SET status='pending', started_at=NULL WHERE status='running'").rowcount


def _dispatch(conn: sqlite3.Connection, client: JQuantsClient, job: sqlite3.Row) -> dict:
    params = json.loads(job["params_json"] or "{}")
    prio = job["priority"]
    if job["job"] == "bars_by_date":
        out = fetch_bars_by_date(conn, client, job["target_date"], prio)
        if out["result"] == "ok":
            out["result"] = out["sync_result"]
        return out
    if job["job"] == "bars_full":
        return fetch_bars_full(conn, client, job["code"], prio, reason=job["reason"] or "queued")
    if job["job"] == "topix_full":
        return fetch_topix(conn, client, priority=prio)
    if job["job"] == "topix_range":
        return fetch_topix(conn, client, params.get("from"), params.get("to"), prio)
    if job["job"] == "fins_by_date":
        from .fins import fetch_fins_by_date

        return fetch_fins_by_date(conn, client, job["target_date"], prio)
    if job["job"] == "fins_full":
        from .fins import fetch_fins_by_code

        return fetch_fins_by_code(conn, client, job["code"], prio, reason=job["reason"] or "queued")
    return {"result": "error", "error_reason": f"unknown job {job['job']}"}


def run_queue(conn: sqlite3.Connection, client: JQuantsClient, max_jobs: int | None = None,
              stop: threading.Event | None = None) -> list[dict]:
    done = []
    while max_jobs is None or len(done) < max_jobs:
        if stop is not None and stop.is_set():
            break
        job = conn.execute(
            "SELECT * FROM fetch_queue WHERE status='pending' ORDER BY priority, id LIMIT 1").fetchone()
        if job is None:
            break
        with conn:
            conn.execute("UPDATE fetch_queue SET status='running', started_at=? WHERE id=?", (utc_now(), job["id"]))
        try:
            out = _dispatch(conn, client, job)
        except Exception as exc:  # keep the worker alive
            log.exception("queue job %s failed", job["id"])
            out = {"result": "error", "error_reason": type(exc).__name__}
        ok = out.get("result") in ("ok", "empty")
        with conn:
            conn.execute(
                "UPDATE fetch_queue SET status=?, finished_at=?, result=?, error_reason=? WHERE id=?",
                ("done" if ok else "failed", utc_now(), out.get("result"), out.get("error_reason"), job["id"]),
            )
        done.append({"id": job["id"], "job": job["job"], "code": job["code"], "target_date": job["target_date"],
                     "result": out.get("result")})
    return done


# --------------------------------------------------------------------------- on-demand

def _lock_for(key: str) -> threading.Lock:
    with _code_locks_guard:
        return _code_locks.setdefault(key, threading.Lock())


def _tail_due(key: str) -> bool:
    last = _tail_checked.get(key)
    return last is None or time.monotonic() - last > TAIL_CHECK_TTL_SECONDS


def ensure_history(conn: sqlite3.Connection, client: JQuantsClient, code: str, now: datetime | None = None) -> dict:
    """On-demand (P1) fetch when a stock page opens. No API call if the code's
    full history is stored and up to date (要件 4.2 / 4.4)."""
    code = resolve_code(code)
    steps: list[dict] = []
    lbd = latest_available_business_day(conn, now)
    with _lock_for(f"bars:{code}"):
        st = history_state(conn, f"bars:{code}")
        if st is None or not st["full_fetched_at"]:
            steps.append({"series": "bars", **fetch_bars_full(conn, client, code, Priority.P1_ON_DEMAND)})
        else:
            last = conn.execute("SELECT MAX(date) FROM daily_bars WHERE code=?", (code,)).fetchone()[0]
            if lbd and last and last < lbd and _tail_due(f"bars:{code}"):
                _tail_checked[f"bars:{code}"] = time.monotonic()
                steps.append({"series": "bars", **fetch_bars_since(conn, client, code, _next_day(last))})
    with _lock_for("topix"):
        st = history_state(conn, "topix")
        if st is None or not st["full_fetched_at"]:
            steps.append({"series": "topix", **fetch_topix(conn, client, priority=Priority.P1_ON_DEMAND)})
        else:
            last = conn.execute("SELECT MAX(date) FROM topix_bars").fetchone()[0]
            if lbd and last and last < lbd and _tail_due("topix"):
                _tail_checked["topix"] = time.monotonic()
                steps.append({"series": "topix",
                              **fetch_topix(conn, client, _next_day(last), lbd, Priority.P1_ON_DEMAND)})
    failed = [s for s in steps if s.get("result") != "ok"]
    has_bars = conn.execute("SELECT 1 FROM daily_bars WHERE code=? LIMIT 1", (code,)).fetchone() is not None
    return {
        "code": code,
        "fetched": bool(steps) and not failed,
        "state": "failed" if failed else ("fetched" if steps else "stored"),
        "has_bars": has_bars,
        "steps": [{k: s.get(k) for k in ("series", "result", "error_reason", "stored")} for s in steps],
        "error_reason": failed[0].get("error_reason") if failed else None,
    }


# --------------------------------------------------------------------------- startup / nightly

def startup_sync(conn: sqlite3.Connection, client: JQuantsClient, now: datetime | None = None) -> dict:
    """Server start: recover stale jobs, refresh the calendar if it does not
    cover today, queue the catch-up for missed business days."""
    from .ingest import fetch_trading_calendar

    reset_stale_running(conn)
    now = (now or now_jst()).astimezone(JST)
    today = now.date().isoformat()
    calendar = None
    if not conn.execute("SELECT 1 FROM trading_calendar WHERE date >= ? LIMIT 1", (today,)).fetchone():
        calendar = fetch_trading_calendar(conn, client, priority=Priority.P4_BACKFILL)["result"]
    plan = queue_catchup(conn, now, Priority.P4_BACKFILL, "catch_up")
    return {"calendar_refresh": calendar, **plan}


def queue_fins_by_date(conn: sqlite3.Connection, end: str | None, priority: int = Priority.P2_NIGHTLY,
                       reason: str = "nightly") -> list[str]:
    """要件 4.2 ④: the day's financial summaries by date, plus the previous
    business day again (values can be finalised overnight; a changed row is
    caught by the content-hash check and logged as a correction)."""
    if end is None:
        return []
    prev = conn.execute(
        "SELECT MAX(date) FROM trading_calendar WHERE is_business_day = 1 AND date < ?", (end,)
    ).fetchone()[0]
    days = [d for d in (prev, end) if d]
    queued = []
    with conn:
        for d in days:
            if enqueue(conn, "fins_by_date", priority, target_date=d, reason=reason):
                queued.append(d)
    return queued


def nightly(conn: sqlite3.Connection, client: JQuantsClient, now: datetime | None = None) -> dict:
    out = queue_catchup(conn, now, Priority.P2_NIGHTLY, "nightly")
    out["fins_queued"] = queue_fins_by_date(conn, out.get("end"))
    # 要件 4.2 ⑨: Saturday = watchlist stocks' summaries re-fetched by code (訂正検知, P3)
    if (now or now_jst()).astimezone(JST).weekday() == 5:
        from .watchlist import queue_weekly_refetch

        out["watchlist_refetch"] = queue_weekly_refetch(conn)
    return out
