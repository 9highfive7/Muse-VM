"""Read-side queries for the /api endpoints (DB only, never calls J-Quants)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .normalize import display_code, normalize_text

JST = timezone(timedelta(hours=9))
SEARCH_LIMIT = 50


def today_jst() -> str:
    return datetime.now(JST).date().isoformat()


# One row per code: the latest master row effective on/before today; if a code
# only has future-dated rows (the API can return the next business day's
# master), fall back to its earliest row.
_LATEST_MASTER_CTE = """
WITH ranked AS (
    SELECT m.*,
           ROW_NUMBER() OVER (
               PARTITION BY code
               ORDER BY CASE WHEN date <= :today THEN 0 ELSE 1 END,
                        CASE WHEN date <= :today THEN date END DESC,
                        date ASC
           ) AS rn
    FROM stock_master m
),
latest AS (SELECT * FROM ranked WHERE rn = 1)
"""


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _stock_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "code": row["code"],
        "display_code": display_code(row["code"]),
        "name": row["name"],
        "name_en": row["name_en"],
        "market": {"code": row["market_code"], "name": row["market_name"]},
        "sector17": {"code": row["sector17_code"], "name": row["sector17_name"]},
        "sector33": {"code": row["sector33_code"], "name": row["sector33_name"]},
        "scale_category": row["scale_category"],
        "master_date": row["date"],
    }


def search_stocks(conn: sqlite3.Connection, q: str, today: str | None = None, limit: int = SEARCH_LIMIT) -> dict:
    norm = normalize_text(q)
    if not norm:
        return {"query": q, "normalized": norm, "count": 0, "results": []}
    pattern = f"%{_like_escape(norm)}%"
    prefix = f"{_like_escape(norm)}%"
    sql = _LATEST_MASTER_CTE + """
    SELECT * FROM latest
    WHERE code LIKE :pattern ESCAPE '\\'
       OR name_norm LIKE :pattern ESCAPE '\\'
       OR COALESCE(name_en_norm, '') LIKE :pattern ESCAPE '\\'
    ORDER BY
       CASE WHEN lower(code) = :q OR (lower(substr(code, 1, 4)) = :q AND length(:q) = 4) THEN 0
            WHEN code LIKE :prefix ESCAPE '\\' THEN 1
            WHEN name_norm LIKE :prefix ESCAPE '\\' THEN 2
            ELSE 3 END,
       code
    LIMIT :limit
    """
    rows = conn.execute(
        sql, {"today": today or today_jst(), "pattern": pattern, "prefix": prefix, "q": norm, "limit": limit}
    ).fetchall()
    return {"query": q, "normalized": norm, "count": len(rows), "results": [_stock_dict(r) for r in rows]}


def resolve_code(code: str) -> str:
    """Accept 4-digit (7203) or 5-digit (72030) input, full-width tolerated."""
    norm = normalize_text(code).upper()
    if len(norm) == 4:
        return norm + "0"
    return norm


def stock_names(conn: sqlite3.Connection, codes: list[str], today: str | None = None) -> dict[str, dict]:
    """code -> {display_code, name} from the latest master row (Phase 4 lists)."""
    if not codes:
        return {}
    rows = conn.execute(_LATEST_MASTER_CTE + "SELECT code, name FROM latest", {"today": today or today_jst()})
    wanted = set(codes)
    out = {r["code"]: {"display_code": display_code(r["code"]), "name": r["name"]} for r in rows if r["code"] in wanted}
    for c in codes:
        out.setdefault(c, {"display_code": display_code(c), "name": None})
    return out


def previous_business_day(conn: sqlite3.Connection, day: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) FROM trading_calendar WHERE is_business_day = 1 AND date < ?", (day,)
    ).fetchone()
    return row[0] if row and row[0] else None


def latest_business_day(conn: sqlite3.Connection, today: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) FROM trading_calendar WHERE is_business_day = 1 AND date <= ?", (today,)
    ).fetchone()
    return row[0] if row and row[0] else None


def stock_header(conn: sqlite3.Connection, code: str, today: str | None = None) -> dict | None:
    today = today or today_jst()
    code5 = resolve_code(code)
    row = conn.execute(_LATEST_MASTER_CTE + "SELECT * FROM latest WHERE code = :code",
                       {"today": today, "code": code5}).fetchone()
    if row is None:
        return None
    stock = _stock_dict(row)
    bars = conn.execute(
        "SELECT date, close, adj_close, adj_factor FROM daily_bars"
        " WHERE code = ? AND adj_close IS NOT NULL ORDER BY date DESC LIMIT 2",
        (code5,),
    ).fetchall()
    if not bars:
        return {
            "state": "not_fetched",
            "stock": stock,
            "price": None,
            "message": "株価データ（日足）はまだ取得されていません。",
        }
    latest = bars[0]
    price: dict[str, Any] = {
        "as_of": latest["date"],
        "close": latest["adj_close"],          # 調整後終値
        "close_unadjusted": latest["close"],
        "prev_date": None,
        "prev_close": None,
        "change": None,
        "change_pct": None,
        "change_unavailable_reason": None,
    }
    if len(bars) == 2 and bars[1]["adj_close"]:
        prev = bars[1]
        change = latest["adj_close"] - prev["adj_close"]
        price.update(
            prev_date=prev["date"],
            prev_close=prev["adj_close"],
            change=round(change, 4),
            change_pct=round(change / prev["adj_close"] * 100, 2),
        )
    else:
        price["change_unavailable_reason"] = "前営業日の終値が未取得のため前日比を算出できません。"
    lbd = latest_business_day(conn, today)
    return {
        "state": "ok",
        "stock": stock,
        "price": price,
        "latest_business_day": lbd,
        # None = unknown (calendar not fetched yet)
        "today_not_reflected": None if lbd is None else latest["date"] < lbd,
    }


def status(conn: sqlite3.Connection, limit: int = 100) -> dict:
    history = [
        {
            "id": r["id"],
            "job": r["job"],
            "endpoint": r["endpoint"],
            "params": json.loads(r["params_json"]) if r["params_json"] else {},
            "priority": r["priority"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
            "result": r["result"],
            "http_status": r["http_status"],
            "error_reason": r["error_reason"],
            "attempts": r["attempts"],
            "row_count": r["row_count"],
        }
        for r in conn.execute("SELECT * FROM fetch_log ORDER BY id DESC LIMIT ?", (limit,))
    ]
    sources = [dict(r) for r in conn.execute("SELECT * FROM series_meta ORDER BY source, series_id")]
    counts = {
        "stock_master_codes": conn.execute("SELECT COUNT(DISTINCT code) FROM stock_master").fetchone()[0],
        "daily_bars": conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0],
        "trading_calendar_days": conn.execute("SELECT COUNT(*) FROM trading_calendar").fetchone()[0],
        "topix_bars": conn.execute("SELECT COUNT(*) FROM topix_bars").fetchone()[0],
        "fins_summary": conn.execute("SELECT COUNT(*) FROM fins_summary").fetchone()[0],
        "fins_corrections": conn.execute("SELECT COUNT(*) FROM fins_revision_log").fetchone()[0],
        "fred_observations": conn.execute("SELECT COUNT(*) FROM fred_observations").fetchone()[0],
    }
    # 訂正検知ログ (要件 2.1 S5 / 4.6): latest detections across all codes
    corrections = [
        {"disclosure_no": r["disclosure_no"], "code": r["code"], "detected_at": r["detected_at"],
         "changes": json.loads(r["changes_json"])}
        for r in conn.execute("SELECT * FROM fins_revision_log ORDER BY id DESC LIMIT 50")
    ]
    window = conn.execute(
        "SELECT COUNT(*) FROM api_request_log WHERE sent_at >= ?",
        ((datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(timespec="milliseconds"),),
    ).fetchone()[0]
    return {
        "history": history,
        "sources": sources,
        "counts": counts,
        "corrections": corrections,
        "requests_last_60s": window,
        "rate_limit_per_minute": 50,
    }

