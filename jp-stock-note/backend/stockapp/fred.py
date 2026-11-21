"""Phase 3 FRED ingestion (要件 4.1 / 4.5): fredgraph.csv, no API key.

* One series per request. ``fredgraph.csv?id=A,B`` returns a ZIP archive
  (Phase 0 finding), so the client never joins ids, and a ZIP body is
  rejected as an error instead of being parsed.
* Missing observations come as ``.`` (legacy format) or an empty field
  (current format, verified 2026-11-21 e.g. ``2025-07-04,`` in DGS2). Both are
  stored as NULL — never 0.
* Rows are sorted by date and de-duplicated (last occurrence wins) before
  storing, so an unsorted or repeated CSV cannot corrupt the series.
* Every fetch writes one ``fetch_log`` row (job ``fred_series``) with its
  HTTP attempts in ``api_request_log``, and updates ``series_meta``
  (source='fred'), exactly like the J-Quants jobs. A failed fetch keeps the
  previously stored observations (要件 4.5「前回値を保持」); each series fails
  independently (要件 9.2).
* HTTP goes through the same ``Transport`` abstraction and ``RequestQueue``
  (retry/backoff) as J-Quants, but on its own queue and token bucket so FRED
  never consumes the J-Quants 50/min budget. The bucket allows one request
  per 1.2 s, which enforces the required ≥1 s gap between series.

Japanese short-term rate (要件 9.1 未決事項7, decided here): the BOJ policy-rate
series IRSTCB01JPM156N stopped in 2023-12 (research-04), so the monthly call
rate IRSTCI01JPM156N (OECD "Immediate rates (<24h): Call money/interbank
rate: Japan", named IRSTCI01 in research-03) is used. Verified on
2026-11-21 that it is live (latest observation 2026-10-01).

CLI:  python -m stockapp.fred [SERIES ...]
"""
from __future__ import annotations

import csv
import io
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from .db import utc_now
from .ingest import _finish, _start
from .jquants.ratelimit import (
    HTTP_ERROR,
    INTERNAL_ERROR,
    NETWORK_ERROR,
    OK,
    PLAN_LIMIT,
    RATE_LIMITED,
    SERVER_ERROR,
    TIMEOUT,
    Attempt,
    Priority,
    RequestQueue,
)
from .jquants.transport import HttpRequest

log = logging.getLogger(__name__)

SERIES_PATH = Path(__file__).resolve().parent / "content" / "macro_series.json"
CSV_PATH = "/graph/fredgraph.csv"
JOB = "fred_series"
MISSING_TOKENS = {"", "."}
DATE_HEADERS = {"observation_date", "date"}

REASONS = {
    PLAN_LIMIT: "403 Forbidden：FREDが取得を拒否しました。",
    RATE_LIMITED: "429 Too Many Requests：FREDの取得制限に達しました（再試行しても解消せず）。",
    SERVER_ERROR: "FRED側のサーバーエラー（5xx）：再試行しても解消しませんでした。",
    TIMEOUT: "タイムアウト：FREDから応答がありませんでした（再試行後）。",
    NETWORK_ERROR: "通信エラー：FREDに接続できませんでした（再試行後）。",
    HTTP_ERROR: "想定外のHTTPエラーです。",
    INTERNAL_ERROR: "内部エラー：送信処理で例外が発生しました（サーバーログを確認してください）。",
    "zip": "FREDの応答がZIP形式でした（複数系列の同時指定とみなされた可能性）。1系列ずつ取得する仕様のため保存していません。",
    "parse": "FREDの応答をCSVとして解釈できませんでした",
}


@lru_cache(maxsize=1)
def config() -> dict:
    return json.loads(SERIES_PATH.read_text(encoding="utf-8"))


def series_defs() -> dict[str, dict]:
    return config()["series"]


SERIES_IDS = ("DGS10", "DGS2", "DEXJPUS", "IRLTLT01JPM156N", "IRSTCI01JPM156N")


class FredError(Exception):
    def __init__(self, outcome: str, status: int | None, reason: str, attempts: list[Attempt]):
        super().__init__(reason)
        self.outcome = outcome
        self.status = status
        self.reason = reason
        self.attempts = attempts


class FredParseError(ValueError):
    pass


@dataclass
class CsvResult:
    text: str
    attempts: list[Attempt]


class FredClient:
    """fredgraph.csv over the injected RequestQueue. No key, no headers."""

    def __init__(self, queue: RequestQueue, base_url: str = "https://fred.stlouisfed.org"):
        self.queue = queue
        self.base_url = base_url.rstrip("/")

    def fetch_csv(self, series_id: str, start: str | None = None,
                  priority: int = Priority.P2_NIGHTLY) -> CsvResult:
        if not series_id or "," in series_id:  # never multi-id: FRED would return a ZIP
            raise ValueError("fetch one FRED series at a time")
        params = {"id": series_id}
        if start:
            params["cosd"] = start
        result = self.queue.execute(HttpRequest("GET", self.base_url + CSV_PATH, params), priority)
        if result.outcome != OK:
            reason = REASONS.get(result.outcome, REASONS[HTTP_ERROR])
            if result.outcome == HTTP_ERROR and result.status is not None:
                reason = f"HTTP {result.status}：{reason}"
            raise FredError(result.outcome, result.status, reason, result.attempts)
        resp = result.response
        text = resp.text if resp is not None else ""
        ctype = (resp.headers.get("content-type") or resp.headers.get("Content-Type") or "") if resp else ""
        if text.startswith("PK") or "zip" in ctype:
            raise FredError(HTTP_ERROR, result.status, REASONS["zip"], result.attempts)
        return CsvResult(text, result.attempts)


# --------------------------------------------------------------------------- parsing

def _valid_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return len(s) == 10
    except ValueError:
        return False


def parse_fred_csv(text: str, series_id: str) -> list[tuple[str, float | None]]:
    """Return [(date, value-or-None)] sorted by date, one row per date.

    The header must be ``observation_date,<ID>`` (current) or ``DATE,<ID>``
    (legacy). ``.`` / empty -> None. Duplicate dates: the last row wins.
    Rows with an unparseable date are skipped; a non-numeric, non-missing
    value is an error (the format changed and must not be guessed)."""
    if text.startswith("﻿"):
        text = text[1:]
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise FredParseError("空の応答") from None
    header = [h.strip() for h in header]
    if len(header) < 2 or header[0].lower() not in DATE_HEADERS or header[1] != series_id:
        raise FredParseError(f"想定外のヘッダー {header[:2]}")
    by_date: dict[str, float | None] = {}
    for row in reader:
        if not row or not row[0].strip():
            continue
        d = row[0].strip()
        if not _valid_date(d):
            continue
        raw = row[1].strip() if len(row) > 1 else ""
        if raw in MISSING_TOKENS:
            by_date[d] = None
            continue
        try:
            by_date[d] = float(raw)
        except ValueError:
            raise FredParseError(f"数値でない値 {raw!r}（{d}）") from None
    return sorted(by_date.items())


# --------------------------------------------------------------------------- storage

def upsert_series_meta(conn: sqlite3.Connection, series_id: str) -> None:
    d = series_defs()[series_id]
    conn.execute(
        "INSERT INTO fred_series(series_id, title, frequency, fetch_cycle, source, source_url, lag_note,"
        " cadence_badge, updated_at) VALUES (?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(series_id) DO UPDATE SET title=excluded.title, frequency=excluded.frequency,"
        " fetch_cycle=excluded.fetch_cycle, source=excluded.source, source_url=excluded.source_url,"
        " lag_note=excluded.lag_note, cadence_badge=excluded.cadence_badge, updated_at=excluded.updated_at",
        (series_id, d["title"], d["frequency"], d["fetch_cycle"], d["source"],
         f"https://fred.stlouisfed.org/series/{series_id}", d["lag_note"], d["cadence_badge"], utc_now()),
    )


def store_observations(conn: sqlite3.Connection, series_id: str, rows: list[tuple[str, float | None]],
                       fetched_at: str) -> dict:
    """Upsert; returns counts and the latest non-missing observation date."""
    existing = {r[0]: r[1] for r in conn.execute(
        "SELECT date, value FROM fred_observations WHERE series_id=?", (series_id,))}
    new = sum(1 for d, _ in rows if d not in existing)
    changed = sum(1 for d, v in rows if d in existing and existing[d] != v)
    conn.executemany(
        "INSERT INTO fred_observations(series_id, date, value, fetched_at) VALUES (?,?,?,?)"
        " ON CONFLICT(series_id, date) DO UPDATE SET value=excluded.value, fetched_at=excluded.fetched_at",
        [(series_id, d, v, fetched_at) for d, v in rows],
    )
    as_of = conn.execute(
        "SELECT MAX(date) FROM fred_observations WHERE series_id=? AND value IS NOT NULL", (series_id,)
    ).fetchone()[0]
    return {"new": new, "changed": changed, "as_of": as_of}


def _start_date(series_id: str, today: date) -> str | None:
    years = series_defs()[series_id].get("history_years")
    if not years:
        return None  # monthly series: whole history (a few hundred rows)
    return (today - timedelta(days=int(years * 365.25))).isoformat()


def fetch_series(conn: sqlite3.Connection, client: FredClient, series_id: str,
                 priority: int = Priority.P2_NIGHTLY, today: date | None = None) -> dict:
    if series_id not in series_defs():
        raise ValueError(f"unknown FRED series {series_id}")
    d = series_defs()[series_id]
    start = _start_date(series_id, today or date.today())
    params = {"id": series_id, **({"cosd": start} if start else {})}
    with conn:
        upsert_series_meta(conn, series_id)
    fetch_id = _start(conn, JOB, params, priority)
    common = {"source": "fred", "series_id": series_id, "update_cycle": d["fetch_cycle"]}
    try:
        res = client.fetch_csv(series_id, start, priority)
        rows = parse_fred_csv(res.text, series_id)
    except FredError as exc:
        with conn:
            _finish(conn, fetch_id, JOB, result=exc.outcome, attempts=exc.attempts, status=exc.status,
                    reason=exc.reason, **common)
        return {"series_id": series_id, "fetch_id": fetch_id, "result": exc.outcome, "error_reason": exc.reason}
    except FredParseError as exc:
        reason = f"{REASONS['parse']}：{exc}"
        with conn:
            _finish(conn, fetch_id, JOB, result="parse_error", attempts=res.attempts, status=200,
                    reason=reason, **common)
        return {"series_id": series_id, "fetch_id": fetch_id, "result": "parse_error", "error_reason": reason}
    except Exception as exc:  # unexpected: record, keep the other series going
        log.exception("FRED %s failed", series_id)
        with conn:
            _finish(conn, fetch_id, JOB, result="error", attempts=[], reason=f"内部エラー: {type(exc).__name__}",
                    **common)
        return {"series_id": series_id, "fetch_id": fetch_id, "result": "error", "error_reason": type(exc).__name__}
    now = utc_now()
    with conn:
        info = store_observations(conn, series_id, rows, now)
        col = "last_change_at" if (info["new"] or info["changed"]) else "last_unchanged_at"
        conn.execute(f"UPDATE fred_series SET {col}=? WHERE series_id=?", (now, series_id))
        _finish(conn, fetch_id, JOB, result="ok", attempts=res.attempts, status=200, row_count=len(rows),
                as_of=info["as_of"], **common)
    return {"series_id": series_id, "fetch_id": fetch_id, "result": "ok", "row_count": len(rows),
            "new": info["new"], "changed": info["changed"], "as_of": info["as_of"],
            "unchanged": not (info["new"] or info["changed"])}


def _last_success(conn: sqlite3.Connection, series_id: str) -> datetime | None:
    row = conn.execute("SELECT last_success_at FROM series_meta WHERE source='fred' AND series_id=?",
                       (series_id,)).fetchone()
    if not row or not row[0]:
        return None
    return datetime.fromisoformat(row[0])


def due_series(conn: sqlite3.Connection, now: datetime | None = None) -> list[str]:
    """要件 4.5 取得頻度: daily series every run; monthly (fetch_cycle weekly)
    only when the last success is 7 days old or older (or never)."""
    now = now or datetime.now(timezone.utc)
    out = []
    for sid in SERIES_IDS:
        if series_defs()[sid]["fetch_cycle"] == "daily":
            out.append(sid)
            continue
        last = _last_success(conn, sid)
        if last is None or now - last >= timedelta(days=7) - timedelta(minutes=5):
            out.append(sid)
    return out


def fetch_all(conn: sqlite3.Connection, client: FredClient, series: list[str] | None = None,
              priority: int = Priority.P2_NIGHTLY, today: date | None = None) -> dict:
    """Fetch each series independently (one request per series; the client's
    bucket spaces them ≥1 s apart). A failure never stops the others."""
    results = [fetch_series(conn, client, sid, priority, today) for sid in (series or list(SERIES_IDS))]
    return {"result": "ok" if all(r["result"] == "ok" for r in results) else "partial"
            if any(r["result"] == "ok" for r in results) else "failed",
            "series": results}


def nightly(conn: sqlite3.Connection, client: FredClient, now: datetime | None = None) -> dict:
    return fetch_all(conn, client, due_series(conn, now), Priority.P2_NIGHTLY)


def startup(conn: sqlite3.Connection, client: FredClient, now: datetime | None = None) -> dict | None:
    """Catch-up on server start: fetch when a daily series has not succeeded
    within the last 20 hours (the PC may have been off at night), plus any
    monthly series that is due."""
    now = now or datetime.now(timezone.utc)
    stale_daily = [sid for sid in SERIES_IDS if series_defs()[sid]["fetch_cycle"] == "daily"
                   and ((last := _last_success(conn, sid)) is None or now - last > timedelta(hours=20))]
    if not stale_daily:
        monthly = [s for s in due_series(conn, now) if series_defs()[s]["fetch_cycle"] != "daily"]
        return fetch_all(conn, client, monthly, Priority.P4_BACKFILL) if monthly else None
    return fetch_all(conn, client, due_series(conn, now), Priority.P4_BACKFILL)


# --------------------------------------------------------------------------- read helpers

def observations(conn: sqlite3.Connection, series_id: str, include_missing: bool = False) -> list[tuple[str, Any]]:
    sql = "SELECT date, value FROM fred_observations WHERE series_id=?"
    if not include_missing:
        sql += " AND value IS NOT NULL"
    return [(r[0], r[1]) for r in conn.execute(sql + " ORDER BY date", (series_id,))]


def fetch_state(conn: sqlite3.Connection, series_id: str) -> dict:
    row = conn.execute("SELECT * FROM series_meta WHERE source='fred' AND series_id=?", (series_id,)).fetchone()
    meta = conn.execute("SELECT last_change_at, last_unchanged_at FROM fred_series WHERE series_id=?",
                        (series_id,)).fetchone()
    return {
        "last_attempt_at": row["last_attempt_at"] if row else None,
        "last_success_at": row["last_success_at"] if row else None,
        "last_result": row["last_result"] if row else None,
        "last_error": row["last_error"] if row else None,
        "last_change_at": meta["last_change_at"] if meta else None,
        "last_unchanged_at": meta["last_unchanged_at"] if meta else None,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .config import load_settings
    from .db import connect, migrate
    from .runtime import build_fred_client

    parser = argparse.ArgumentParser(description="Fetch FRED series into data/app.db (no API key)")
    parser.add_argument("series", nargs="*", help=f"default: all of {', '.join(SERIES_IDS)}")
    args = parser.parse_args(argv)
    unknown = [s for s in args.series if s not in SERIES_IDS]
    if unknown:
        parser.error(f"unknown series {unknown}")
    settings = load_settings()
    conn = connect(settings.db_path)
    migrate(conn)
    out = fetch_all(conn, build_fred_client(settings), args.series or None, Priority.P4_BACKFILL)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out["result"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
