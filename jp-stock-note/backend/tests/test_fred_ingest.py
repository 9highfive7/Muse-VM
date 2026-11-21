"""Phase 3 FRED ingestion: CSV parsing ('.' / empty missing values, unsorted,
duplicate dates), one-series-per-request fetching through the shared
Transport abstraction, logging like the J-Quants jobs, per-series failure
isolation with the previous values kept (受け入れ条件③), fetch frequencies."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from fred_fixtures import FredHandler, csv_text, make_fred_client, seed_fred, standard_bodies
from stockapp import fred, macro
from stockapp.fred import FredParseError, parse_fred_csv
from stockapp.jquants.transport import TransportTimeout


# --------------------------------------------------------------------------- parsing

def test_parse_missing_values_dot_and_empty_are_null():
    text = "observation_date,DGS10\n2026-11-17,4.10\n2026-11-18,.\n2026-11-19,\n2026-11-20,4.12\n"
    assert parse_fred_csv(text, "DGS10") == [
        ("2026-11-17", 4.10), ("2026-11-18", None), ("2026-11-19", None), ("2026-11-20", 4.12)]


def test_parse_sorts_and_deduplicates_last_wins():
    text = csv_text("DEXJPUS", [("2026-11-13", 159.9), ("2026-11-11", 158.0), ("2026-11-12", 158.5),
                                ("2026-11-11", 158.1), ("2026-11-13", "."), ("2026-11-13", 159.93)])
    assert parse_fred_csv(text, "DEXJPUS") == [("2026-11-11", 158.1), ("2026-11-12", 158.5), ("2026-11-13", 159.93)]


def test_parse_legacy_header_bom_and_blank_lines():
    text = "﻿DATE,IRLTLT01JPM156N\r\n2026-09-01,1.60\r\n\r\n2026-10-01,1.65\r\n"
    assert parse_fred_csv(text, "IRLTLT01JPM156N") == [("2026-09-01", 1.6), ("2026-10-01", 1.65)]


def test_parse_skips_invalid_dates():
    text = "observation_date,DGS2\n2026-13-01,3.0\nnot-a-date,3.1\n2026-11-20,3.2\n"
    assert parse_fred_csv(text, "DGS2") == [("2026-11-20", 3.2)]


@pytest.mark.parametrize("text", [
    "",                                                  # empty body
    "observation_date,DGS2\n2026-11-20,4.1\n",           # wrong series column
    "<html>error</html>\n",                              # not a CSV
])
def test_parse_rejects_unexpected_header(text):
    with pytest.raises(FredParseError):
        parse_fred_csv(text, "DGS10")


def test_parse_rejects_non_numeric_value():
    with pytest.raises(FredParseError):
        parse_fred_csv("observation_date,DGS10\n2026-11-20,N/A\n", "DGS10")


# --------------------------------------------------------------------------- fetching

def test_fetch_all_one_series_per_request_spaced_and_logged(db_conn):
    client, transport, clock = make_fred_client(FredHandler(standard_bodies()))
    out = fred.fetch_all(db_conn, client, today=date(2026, 11, 21))
    assert out["result"] == "ok"
    assert [r.params["id"] for r in transport.requests] == list(fred.SERIES_IDS)
    for r in transport.requests:
        assert r.url == "https://fred.example.invalid/graph/fredgraph.csv"
        assert "," not in r.params["id"] and not r.headers  # keyless, no headers
    # daily series limited to 6 years, monthly series fetched whole
    assert transport.requests[0].params["cosd"] == "2020-11-21"
    assert "cosd" not in transport.requests[3].params
    gaps = [b - a for a, b in zip(transport.sent_at, transport.sent_at[1:])]
    assert gaps and min(gaps) >= 1.0          # ≥1 s between series (要件 4.5)

    logs = db_conn.execute("SELECT * FROM fetch_log ORDER BY id").fetchall()
    assert [r["job"] for r in logs] == ["fred_series"] * 5
    assert all(r["endpoint"] == "/graph/fredgraph.csv" and r["result"] == "ok" and r["attempts"] == 1 for r in logs)
    assert db_conn.execute("SELECT COUNT(*) FROM api_request_log").fetchone()[0] == 5
    meta = {r["series_id"]: dict(r) for r in db_conn.execute("SELECT * FROM series_meta WHERE source='fred'")}
    assert set(meta) == set(fred.SERIES_IDS)
    assert meta["DEXJPUS"]["as_of_date"] == "2026-11-13" and meta["DEXJPUS"]["update_cycle"] == "daily"
    assert meta["IRLTLT01JPM156N"]["as_of_date"] == "2026-10-01"
    assert meta["IRLTLT01JPM156N"]["update_cycle"] == "weekly"
    desc = {r["series_id"]: dict(r) for r in db_conn.execute("SELECT * FROM fred_series")}
    assert desc["IRLTLT01JPM156N"]["frequency"] == "monthly"
    assert "1週" in desc["DEXJPUS"]["lag_note"] and desc["DEXJPUS"]["source"].startswith("FRED")


def test_missing_observations_stored_as_null_and_as_of_skips_them(db_conn):
    bodies = standard_bodies()
    bodies["DGS10"] = bodies["DGS10"][:-1] + [(bodies["DGS10"][-1][0], None)]   # last day missing
    seed_fred(db_conn, bodies)
    last_day = bodies["DGS10"][-1][0]
    row = db_conn.execute("SELECT value FROM fred_observations WHERE series_id='DGS10' AND date=?",
                          (last_day,)).fetchone()
    assert row is not None and row["value"] is None            # kept as missing, not 0
    as_of = db_conn.execute("SELECT as_of_date FROM series_meta WHERE series_id='DGS10'").fetchone()[0]
    assert as_of == bodies["DGS10"][-2][0]


def test_failure_is_isolated_and_previous_values_kept(db_conn):
    seed_fred(db_conn)
    before = db_conn.execute("SELECT COUNT(*) FROM fred_observations WHERE series_id='DGS10'").fetchone()[0]
    client, transport, _ = make_fred_client(
        FredHandler(standard_bodies(), fail={"DGS10": 503, "DEXJPUS": TransportTimeout("t")}))
    out = fred.fetch_all(db_conn, client, today=date(2026, 11, 21))
    res = {r["series_id"]: r["result"] for r in out["series"]}
    assert res == {"DGS10": "server_error", "DGS2": "ok", "DEXJPUS": "timeout",
                   "IRLTLT01JPM156N": "ok", "IRSTCI01JPM156N": "ok"}
    assert out["result"] == "partial"
    assert sum(1 for r in transport.requests if r.params["id"] == "DGS10") == 4   # 1 + 3 retries
    after = db_conn.execute("SELECT COUNT(*) FROM fred_observations WHERE series_id='DGS10'").fetchone()[0]
    assert after == before                                    # previous values kept
    m = macro.macro_payload(db_conn)
    us = m["items"]["us10y"]
    assert us["state"] == "ok" and us["value"] is not None
    assert us["fetch"]["failed"] is True and us["fetch"]["status_text"].startswith("取得失敗・前回値を表示中")
    assert m["items"]["us_spread"]["fetch"]["failed"] is True
    assert m["items"]["jp10y"]["fetch"]["failed"] is False
    reason = db_conn.execute("SELECT last_error FROM series_meta WHERE series_id='DEXJPUS'").fetchone()[0]
    assert "タイムアウト" in reason


def test_zip_response_is_rejected(db_conn):
    client, _, _ = make_fred_client(FredHandler(standard_bodies(), zip_for={"DGS2"}))
    out = fred.fetch_series(db_conn, client, "DGS2", today=date(2026, 11, 21))
    assert out["result"] == "http_error" and "ZIP" in out["error_reason"]
    assert db_conn.execute("SELECT COUNT(*) FROM fred_observations").fetchone()[0] == 0


def test_client_refuses_multi_id():
    client, _, _ = make_fred_client(FredHandler({}))
    with pytest.raises(ValueError):
        client.fetch_csv("DGS10,DGS2")


def test_parse_error_is_logged_not_raised(db_conn):
    class Bad(FredHandler):
        def __call__(self, request):
            from stockapp.jquants.transport import HttpResponse
            return HttpResponse(200, None, "<html>maintenance</html>", {"content-type": "text/html"})

    client, _, _ = make_fred_client(Bad({}))
    out = fred.fetch_series(db_conn, client, "DGS10", today=date(2026, 11, 21))
    assert out["result"] == "parse_error"
    assert db_conn.execute("SELECT result FROM fetch_log").fetchone()[0] == "parse_error"


def test_unchanged_fetch_recorded(db_conn):
    seed_fred(db_conn)
    client, _, _ = make_fred_client(FredHandler(standard_bodies()))
    out = fred.fetch_series(db_conn, client, "DEXJPUS", today=date(2026, 11, 21))
    assert out["unchanged"] is True and out["new"] == 0
    row = db_conn.execute("SELECT last_unchanged_at FROM fred_series WHERE series_id='DEXJPUS'").fetchone()
    assert row[0] is not None


def test_due_series_monthly_weekly(db_conn):
    now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    assert fred.due_series(db_conn, now) == list(fred.SERIES_IDS)          # never fetched
    seed_fred(db_conn)
    with db_conn:
        db_conn.execute("UPDATE series_meta SET last_success_at=? WHERE source='fred'",
                        ((now - timedelta(days=2)).isoformat(),))
    assert fred.due_series(db_conn, now) == ["DGS10", "DGS2", "DEXJPUS"]
    later = now + timedelta(days=6)
    assert fred.due_series(db_conn, later) == list(fred.SERIES_IDS)


# --------------------------------------------------------------------------- API

@pytest.fixture
def fred_api(make_settings):
    from fastapi.testclient import TestClient

    from stockapp.main import create_app

    client, transport, _ = make_fred_client(FredHandler(standard_bodies(), fail={"IRSTCI01JPM156N": 500}))
    app = create_app(make_settings(), fred_client=client)
    return TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)), transport


def test_fetch_endpoint_runs_all_series_and_status_shows_them(fred_api):
    api, transport = fred_api
    r = api.post("/api/fetch-fred")
    assert r.status_code == 200 and r.json()["result"] == "partial"
    assert {q.params["id"] for q in transport.requests} == set(fred.SERIES_IDS)
    st = api.get("/api/status").json()
    fred_rows = {s["series_id"]: s for s in st["sources"] if s["source"] == "fred"}
    assert fred_rows["IRSTCI01JPM156N"]["last_result"] == "server_error"
    assert fred_rows["DGS10"]["last_result"] == "ok"
    assert st["counts"]["fred_observations"] > 0 and len(st["fred_series"]) == 5
    assert st["tankan"]["value"] == pytest.approx(154.23)
    # the stock-independent parts of the app keep working (受け入れ条件③)
    assert api.get("/api/macro").status_code == 200
    assert api.get("/api/search?q=x").status_code == 200


def test_fetch_endpoint_single_series_and_unknown(fred_api):
    api, transport = fred_api
    assert api.post("/api/fetch-fred?series=DGS2").json()["series"][0]["series_id"] == "DGS2"
    assert [q.params["id"] for q in transport.requests] == ["DGS2"]
    assert api.post("/api/fetch-fred?series=T10Y2Y").status_code == 404
