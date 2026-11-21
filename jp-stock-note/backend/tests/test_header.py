"""S3 header endpoint (要件 2.3 / 3.1)."""
from __future__ import annotations

import pytest

from conftest import TODAY, seed
from stockapp.db import connect
from stockapp.queries import stock_header


def test_header_ok_uses_adjusted_close(seeded_conn):
    h = stock_header(seeded_conn, "7203", today=TODAY)
    assert h["state"] == "ok"
    s = h["stock"]
    assert (s["code"], s["display_code"], s["name"]) == ("72030", "7203", "トヨタ自動車")
    assert s["market"]["name"] == "プライム"
    assert s["sector17"]["name"] == "自動車・輸送機"
    assert s["sector33"]["name"] == "輸送用機器"
    p = h["price"]
    assert p["as_of"] == "2026-11-20"
    assert p["close"] == 3051.0
    assert p["prev_date"] == "2026-11-19"
    assert p["change"] == pytest.approx(21.0)
    assert p["change_pct"] == pytest.approx(round(21.0 / 3030.0 * 100, 2))


def test_change_is_computed_from_adjusted_series(seeded_conn):
    h = stock_header(seeded_conn, "99840", today=TODAY)
    p = h["price"]
    assert p["close"] == 5100.0              # AdjC, not raw C (20400)
    assert p["close_unadjusted"] == 20400.0
    assert p["change"] == pytest.approx(100.0)
    assert p["change_pct"] == pytest.approx(2.0)


def test_single_bar_gives_no_fabricated_change(seeded_conn):
    h = stock_header(seeded_conn, "72670", today=TODAY)
    assert h["state"] == "ok"
    assert h["price"]["change"] is None and h["price"]["change_pct"] is None
    assert h["price"]["change_unavailable_reason"]


def test_not_fetched_state(seeded_conn):
    h = stock_header(seeded_conn, "94330", today=TODAY)
    assert h["state"] == "not_fetched"
    assert h["price"] is None
    assert h["stock"]["name"] == "ＫＤＤＩ"


def test_unknown_code_returns_none(seeded_conn):
    assert stock_header(seeded_conn, "00000", today=TODAY) is None


def test_today_not_reflected_flag(seeded_conn):
    # Saturday 11-21: latest business day is 11-20 = as_of -> reflected
    assert stock_header(seeded_conn, "7203", today=TODAY)["today_not_reflected"] is False
    # On business day 11-24 the stored 11-20 bar is stale
    h = stock_header(seeded_conn, "7203", today="2026-11-24")
    assert h["today_not_reflected"] is True
    assert h["latest_business_day"] == "2026-11-24"


def test_today_not_reflected_unknown_without_calendar(db_conn):
    seed(db_conn, calendar=False)
    assert stock_header(db_conn, "7203", today=TODAY)["today_not_reflected"] is None


def test_header_endpoint_states(make_settings, make_client):
    settings = make_settings()
    client = make_client(settings)
    conn = connect(settings.db_path)
    seed(conn)
    conn.close()

    ok = client.get("/api/stocks/72030/header")
    assert ok.status_code == 200 and ok.json()["state"] == "ok"
    assert ok.json()["price"]["close"] == 3051.0

    four_digit = client.get("/api/stocks/7203/header")
    assert four_digit.json()["stock"]["code"] == "72030"

    nf = client.get("/api/stocks/9433/header")
    assert nf.status_code == 200
    body = nf.json()
    assert body["state"] == "not_fetched" and body["price"] is None

    missing = client.get("/api/stocks/1111/header")
    assert missing.status_code == 404


def test_header_endpoint_with_empty_db_is_not_fabricated(make_client):
    client = make_client()
    assert client.get("/api/stocks/7203/header").status_code == 404
