"""受け入れ条件⑥: PCを3営業日停止した後に起動すると、未取得の3日分が自動で補完される。
Plus the on-demand fetch rule (要件 4.2 / 4.4: stored and current -> no API call).

Calendar fixture: 2026-11 with weekends and 勤労感謝の日 (11-23, Mon).
Last successful by-date sync: 2026-11-18 (Wed). Startup: 2026-11-24 (Tue) 21:00 JST.
Missing business days: 11-19, 11-20, 11-24 (11-21/22 weekend, 11-23 holiday).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from conftest import make_jq_client
from stockapp import bars
from stockapp.ingest import store_trading_calendar
from stockapp.jquants.client import ENDPOINT_CALENDAR, ENDPOINT_DAILY_BARS, ENDPOINT_TOPIX
from stockapp.jquants.ratelimit import Priority
from stockapp.jquants.transport import HttpResponse

JST = bars.JST
HOLIDAYS = {"2026-11-03", "2026-11-23"}
STARTUP = datetime(2026, 11, 24, 21, 0, tzinfo=JST)
MISSING = ["2026-11-19", "2026-11-20", "2026-11-24"]


def calendar_rows(start="2026-11-01", end="2026-12-31"):
    d, out = date.fromisoformat(start), []
    while d.isoformat() <= end:
        iso = d.isoformat()
        out.append({"Date": iso, "HolDiv": "0" if d.weekday() >= 5 or iso in HOLIDAYS else "1"})
        d += timedelta(days=1)
    return out


def bar(code, day, c=1000.0, factor=1.0):
    return {"Date": day, "Code": code, "O": c, "H": c + 5, "L": c - 5, "C": c, "UL": "0", "LL": "0",
            "Vo": 1000.0, "Va": c * 1000, "AdjFactor": factor, "AdjO": c, "AdjH": c + 5, "AdjL": c - 5,
            "AdjC": c, "AdjVo": 1000.0, "MktCap": None}


def handler(req):
    p = req.params
    if req.url.endswith(ENDPOINT_DAILY_BARS) and "date" in p:
        return HttpResponse(200, {"data": [bar("72030", p["date"]), bar("99840", p["date"], 5000.0)]})
    if req.url.endswith(ENDPOINT_DAILY_BARS) and "code" in p:
        days = [r["Date"] for r in calendar_rows("2026-11-02", "2026-11-24") if r["HolDiv"] == "1"]
        return HttpResponse(200, {"data": [bar(p["code"], d) for d in days]})
    if req.url.endswith(ENDPOINT_TOPIX):
        days = [r["Date"] for r in calendar_rows("2026-11-02", "2026-11-24") if r["HolDiv"] == "1"]
        return HttpResponse(200, {"data": [{"Date": d, "O": 3000, "H": 3010, "L": 2990, "C": 3000} for d in days]})
    if req.url.endswith(ENDPOINT_CALENDAR):
        return HttpResponse(200, {"data": calendar_rows()})
    return HttpResponse(404, {"message": "unexpected"})


@pytest.fixture
def stopped_pc(db_conn):
    """State left behind by a PC that last synced on 2026-11-18."""
    with db_conn:
        store_trading_calendar(db_conn, calendar_rows(), "2026-11-01T00:00:00+00:00")
        for d in [r["Date"] for r in calendar_rows("2026-11-02", "2026-11-18") if r["HolDiv"] == "1"]:
            db_conn.execute("INSERT INTO daily_sync(date, result, row_count, updated_at) VALUES (?, 'ok', 2, 'x')", (d,))
            db_conn.executemany(
                "INSERT INTO daily_bars(code, date, close, adj_close, adj_factor, fetched_at) VALUES (?,?,1000,1000,1,'x')",
                [("72030", d), ("99840", d)])
    return db_conn


def bar_dates_requested(transport):
    return [r.params["date"] for r in transport.requests if r.url.endswith(ENDPOINT_DAILY_BARS) and "date" in r.params]


def test_plan_lists_exactly_the_missing_business_days(stopped_pc):
    plan = bars.plan_catchup(stopped_pc, STARTUP)
    assert plan["last_success"] == "2026-11-18" and plan["end"] == "2026-11-24"
    assert plan["dates"] == MISSING
    # before 20:00 JST today's bars are not expected yet
    assert bars.plan_catchup(stopped_pc, STARTUP.replace(hour=10))["dates"] == MISSING[:2]


def test_startup_catchup_queues_and_fills_exactly_three_days(stopped_pc, fake_transport_cls):
    transport = fake_transport_cls(handler=handler)
    client = make_jq_client(transport)

    out = bars.startup_sync(stopped_pc, client, STARTUP)
    assert out["calendar_refresh"] is None            # calendar covers today: no refresh request
    assert out["queued"] == MISSING
    assert transport.requests == []                   # startup only queues; the worker fetches
    pending = bars.pending_jobs(stopped_pc)
    by_date = [j for j in pending if j["job"] == "bars_by_date"]
    assert [j["target_date"] for j in by_date] == MISSING
    assert {j["priority"] for j in by_date} == {Priority.P4_BACKFILL}
    assert {j["reason"] for j in by_date} == {"catch_up"}

    done = bars.run_queue(stopped_pc, client)
    assert all(d["result"] == "ok" for d in done)
    assert bar_dates_requested(transport) == MISSING  # one by-date request per missing day, nothing else
    assert bars.pending_jobs(stopped_pc) == []
    synced = [r[0] for r in stopped_pc.execute(
        "SELECT date FROM daily_sync WHERE result='ok' AND date > '2026-11-18' ORDER BY date")]
    assert synced == MISSING
    stored = [r[0] for r in stopped_pc.execute(
        "SELECT DISTINCT date FROM daily_bars WHERE code='72030' AND date > '2026-11-18' ORDER BY date")]
    assert stored == MISSING

    # second start: nothing left to catch up, no requests
    before = len(transport.requests)
    again = bars.startup_sync(stopped_pc, client, STARTUP + timedelta(minutes=5))
    assert again["queued"] == [] and again["dates"] == []
    assert not [j for j in bars.pending_jobs(stopped_pc) if j["job"] == "bars_by_date"]
    bars.run_queue(stopped_pc, client)
    assert bar_dates_requested(transport)[before:] == []


def test_unpublished_day_is_retried_on_next_start(stopped_pc, fake_transport_cls):
    """A day whose by-date response is still empty stays 'missing'."""
    def h(req):
        if "date" in req.params and req.params["date"] == "2026-11-24":
            return HttpResponse(200, {"data": []})
        return handler(req)

    transport = fake_transport_cls(handler=h)
    client = make_jq_client(transport)
    bars.startup_sync(stopped_pc, client, STARTUP)
    bars.run_queue(stopped_pc, client)
    assert bars.plan_catchup(stopped_pc, STARTUP)["dates"] == ["2026-11-24"]


def test_startup_refreshes_calendar_when_it_does_not_cover_today(db_conn, fake_transport_cls):
    transport = fake_transport_cls(handler=handler)
    out = bars.startup_sync(db_conn, make_jq_client(transport), STARTUP)
    assert out["calendar_refresh"] == "ok"
    assert transport.requests[0].url.endswith(ENDPOINT_CALENDAR)
    assert out["queued"] == ["2026-11-24"]   # fresh install: only the latest business day


def test_failed_catchup_day_is_reported_and_requeued(stopped_pc, fake_transport_cls):
    transport = fake_transport_cls(handler=lambda req: HttpResponse(403, {"message": "plan"}))
    client = make_jq_client(transport)
    bars.startup_sync(stopped_pc, client, STARTUP)
    done = bars.run_queue(stopped_pc, client)
    assert {d["result"] for d in done if d["job"] == "bars_by_date"} == {"plan_limit"}
    failed = stopped_pc.execute("SELECT COUNT(*) FROM fetch_queue WHERE status='failed'").fetchone()[0]
    assert failed >= 3
    assert bars.queue_catchup(stopped_pc, STARTUP)["queued"] == MISSING


def test_on_demand_history_fetch_then_db_only(stopped_pc, fake_transport_cls):
    transport = fake_transport_cls(handler=handler)
    client = make_jq_client(transport)
    first = bars.ensure_history(stopped_pc, client, "1301", STARTUP)
    assert first["state"] == "fetched" and first["has_bars"] is True
    codes = [r.params.get("code") for r in transport.requests if r.url.endswith(ENDPOINT_DAILY_BARS)]
    assert codes == ["13010"]
    assert any(r.url.endswith(ENDPOINT_TOPIX) for r in transport.requests)

    n = len(transport.requests)
    second = bars.ensure_history(stopped_pc, client, "13010", STARTUP)
    assert second["state"] == "stored" and second["steps"] == []
    assert len(transport.requests) == n               # stored and current: no API call
