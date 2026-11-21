"""J-Quants client + ingest jobs + /api/status, all over a fake transport."""
from __future__ import annotations

import json
import logging

from conftest import load_fixture
from stockapp.db import connect
from stockapp.ingest import fetch_stock_master, fetch_trading_calendar
from stockapp.jquants.client import ENDPOINT_CALENDAR, ENDPOINT_MASTER, JQuantsClient
from stockapp.jquants.ratelimit import RequestQueue, TokenBucket
from stockapp.jquants.transport import FakeClock, HttpResponse, TransportTimeout

KEY = "test-key-DO-NOT-LEAK-0123456789"


def jq_client(transport, clock, key=KEY):
    queue = RequestQueue(transport, TokenBucket(clock=clock), clock=clock, backoff_base_seconds=1.0)
    return JQuantsClient(key, queue, "https://jq.example.invalid")


def dump_db(conn) -> str:
    out = []
    for t in ("fetch_log", "api_request_log", "series_meta", "stock_master", "trading_calendar"):
        out += [json.dumps(dict(r), ensure_ascii=False) for r in conn.execute(f"SELECT * FROM {t}")]
    return "\n".join(out)


def test_master_fetch_with_pagination(db_conn, fake_transport_cls, caplog):
    caplog.set_level(logging.DEBUG)
    rows = load_fixture("master.json")["data"]
    clock = FakeClock()
    transport = fake_transport_cls(
        clock=clock,
        responses=[
            HttpResponse(200, {"data": rows[:4], "pagination_key": "page2"}),
            HttpResponse(200, {"data": rows[4:]}),
        ],
    )
    out = fetch_stock_master(db_conn, jq_client(transport, clock))
    assert out["result"] == "ok" and out["row_count"] == len(rows)

    first, second = transport.requests
    assert first.url == "https://jq.example.invalid" + ENDPOINT_MASTER
    assert first.headers["x-api-key"] == KEY
    assert "pagination_key" not in first.params
    assert second.params["pagination_key"] == "page2"

    assert db_conn.execute("SELECT COUNT(*) FROM stock_master").fetchone()[0] == len(rows)
    log = db_conn.execute("SELECT * FROM fetch_log").fetchone()
    assert (log["result"], log["attempts"], log["row_count"]) == ("ok", 2, len(rows))
    assert log["finished_at"] is not None
    assert db_conn.execute("SELECT COUNT(*) FROM api_request_log").fetchone()[0] == 2
    meta = db_conn.execute("SELECT * FROM series_meta WHERE series_id='stock_master'").fetchone()
    assert meta["last_success_at"] and meta["as_of_date"] == "2026-11-24"

    # the key must never reach logs or the DB
    assert KEY not in caplog.text
    assert KEY not in dump_db(db_conn)


def test_calendar_fetch_maps_holdiv(db_conn, fake_transport_cls):
    clock = FakeClock()
    data = [
        {"Date": "2026-11-01", "HolDiv": "0"},
        {"Date": "2026-11-02", "HolDiv": "1"},
        {"Date": "2026-12-30", "HolDiv": "2"},
        {"Date": "2026-11-03", "HolDiv": "3"},
    ]
    transport = fake_transport_cls(clock=clock, responses=[HttpResponse(200, {"data": data})])
    out = fetch_trading_calendar(db_conn, jq_client(transport, clock), "2026-11-01", "2026-12-31")
    assert out["result"] == "ok"
    assert transport.requests[0].url.endswith(ENDPOINT_CALENDAR)
    assert transport.requests[0].params == {"from": "2026-11-01", "to": "2026-12-31"}
    got = dict(db_conn.execute("SELECT date, is_business_day FROM trading_calendar").fetchall())
    assert got == {"2026-11-01": 0, "2026-11-02": 1, "2026-12-30": 1, "2026-11-03": 0}


def test_no_api_key_makes_no_request(db_conn, fake_transport_cls):
    clock = FakeClock()
    transport = fake_transport_cls(clock=clock)
    out = fetch_stock_master(db_conn, jq_client(transport, clock, key=None))
    assert out["result"] == "no_api_key"
    assert transport.requests == []
    assert db_conn.execute("SELECT result FROM fetch_log").fetchone()[0] == "no_api_key"


def test_403_recorded_as_plan_limit(db_conn, fake_transport_cls):
    clock = FakeClock()
    transport = fake_transport_cls(clock=clock, responses=[HttpResponse(403, {"message": "plan"})])
    out = fetch_stock_master(db_conn, jq_client(transport, clock))
    assert out["result"] == "plan_limit"
    assert len(transport.requests) == 1
    log = db_conn.execute("SELECT * FROM fetch_log").fetchone()
    assert log["http_status"] == 403 and "403" in log["error_reason"]
    meta = db_conn.execute("SELECT * FROM series_meta").fetchone()
    assert meta["last_result"] == "plan_limit" and meta["last_success_at"] is None


def test_error_message_echoing_key_is_scrubbed(db_conn, fake_transport_cls):
    clock = FakeClock()
    transport = fake_transport_cls(clock=clock, responses=[HttpResponse(400, {"message": f"bad key {KEY}"})])
    out = fetch_stock_master(db_conn, jq_client(transport, clock))
    assert out["result"] == "http_error"
    assert KEY not in out["error_reason"]
    assert KEY not in dump_db(db_conn)


def test_failed_fetch_keeps_previous_rows(db_conn, fake_transport_cls):
    rows = load_fixture("master.json")["data"]
    clock = FakeClock()
    transport = fake_transport_cls(
        clock=clock, responses=[HttpResponse(200, {"data": rows})] + [HttpResponse(503)] * 4
    )
    client = jq_client(transport, clock)
    assert fetch_stock_master(db_conn, client)["result"] == "ok"
    assert fetch_stock_master(db_conn, client)["result"] == "server_error"
    assert db_conn.execute("SELECT COUNT(*) FROM stock_master").fetchone()[0] == len(rows)
    meta = db_conn.execute("SELECT * FROM series_meta").fetchone()
    assert meta["last_result"] == "server_error" and meta["last_success_at"] is not None


def test_status_endpoint_shows_causes(make_settings, make_client, fake_transport_cls):
    """受け入れ条件⑤: 403・429・timeout each surface a cause via /api/status."""
    clock = FakeClock()
    transport = fake_transport_cls(
        clock=clock,
        responses=[HttpResponse(403)] + [HttpResponse(429)] * 4 + [TransportTimeout("t")] * 4,
    )
    settings = make_settings(api_key=KEY)
    client = make_client(settings, jq_client=jq_client(transport, clock))
    for _ in range(3):
        assert client.post("/api/fetch/master").status_code == 200
    body = client.get("/api/status")
    assert body.status_code == 200
    data = body.json()
    results = [h["result"] for h in data["history"]]
    assert results == ["timeout", "rate_limited", "plan_limit"]  # newest first
    reasons = {h["result"]: h["error_reason"] for h in data["history"]}
    assert "403" in reasons["plan_limit"]
    assert "429" in reasons["rate_limited"]
    assert "タイムアウト" in reasons["timeout"]
    assert data["api_key_configured"] is True
    assert data["sources"][0]["series_id"] == "stock_master"
    assert KEY not in body.text


def test_status_endpoint_empty_db(make_client):
    data = make_client().get("/api/status").json()
    assert data["history"] == [] and data["api_key_configured"] is False


def test_migration_is_wal_and_idempotent(tmp_path):
    from stockapp.db import migrate

    conn = connect(tmp_path / "x.db")
    assert migrate(conn) == ["0001_init", "0002_phase1_bars", "0003_phase2_fins", "0004_phase3_macro",
                            "0005_phase4_memo_watchlist"]
    assert migrate(conn) == []
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "stock_master", "daily_bars", "topix_bars", "fins_summary", "fetch_log",
        "api_request_log", "series_meta", "trading_calendar",
        "fred_observations", "fred_series", "app_settings",
    } <= tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fins_summary)")}
    assert {"raw_json", "content_sha256", "disclosure_no"} <= cols
    cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_bars)")}
    assert {"close", "adj_close", "adj_factor"} <= cols
