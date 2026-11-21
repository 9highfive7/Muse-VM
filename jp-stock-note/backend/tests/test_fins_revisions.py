"""要件 5.3 予想修正履歴・予想の型 and 要件 4.6 訂正検知（受け入れ条件⑦）."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import fins_fixtures as fx
from conftest import FakeTransport, make_jq_client
from fins_fixtures import OKU, stmt
from stockapp import fins, financials, fundamentals as fm
from stockapp.jquants.client import ENDPOINT_FINS_SUMMARY
from stockapp.jquants.transport import HttpResponse

CODE = "10050"


def typed_company(specs, code=CODE, first_year=2022):
    """specs: [(initial_op, [revised_op, ...], actual_op)] for consecutive 3月期 years.
    The initial forecast is the previous FY statement's next-year forecast."""
    rows = []
    base_s, base_e = fx.fy_dates(first_year - 1)
    nx_s, nx_e = fx.fy_dates(first_year)
    rows.append(stmt(code, "BASE", f"{first_year - 1}-05-10", "FY", base_s, base_e, sales=1000, op=100, np_=60,
                     nx_start=nx_s, nx_end=nx_e, nx_op=specs[0][0], nx_sales=1000, nx_np=60))
    for i, (init, revs, actual) in enumerate(specs):
        y = first_year + i
        fs, fe = fx.fy_dates(y)
        for j, rv in enumerate(revs):
            rows.append(fx.revision(code, f"R{y}-{j}", f"{y - 1}-1{j}-20", fs, fe, f_op=rv))
        nxt = specs[i + 1][0] if i + 1 < len(specs) else 100
        ns, ne = fx.fy_dates(y + 1)
        rows.append(stmt(code, f"FY{y}", f"{y}-05-10", "FY", fs, fe, sales=1000, op=actual, np_=60,
                         nx_start=ns, nx_end=ne, nx_op=nxt, nx_sales=1000, nx_np=60))
    return rows


def classify(conn):
    return financials.financials_payload(conn, CODE)["revisions"]["type"]


@pytest.mark.parametrize("specs,expected,m", [
    ([(100, [110], 112)] * 3, "conservative_upward", 3),                               # +12% ×3, upward revisions
    ([(100, [90], 88)] * 3, "aggressive_downward", 3),                                 # −12% ×3
    ([(100, [], 102), (100, [], 99), (100, [], 103)], "stable", 3),                     # all within ±5%
    ([(100, [110], 112), (100, [90], 88), (100, [], 102)], "volatile", 1),              # +12 / −12 / +2
])
def test_forecast_type_classification(db_conn, specs, expected, m):
    fx.store(db_conn, typed_company(specs))
    t = classify(db_conn)
    assert t["type"] == expected
    assert t["n"] == 3 and t["m"] == m
    assert "3期中" in t["text"]
    assert t["counts_text"].startswith("完了済み3期のうち")


def test_landing_rates_and_revision_rates_hand_computed(db_conn):
    fx.store(db_conn, typed_company([(100, [110], 112)] * 3))
    rev = financials.financials_payload(db_conn, CODE)["revisions"]
    y = next(y for y in rev["years"] if y["fy_end"] == "2022-03-31")
    assert y["landing_pct_operating_profit"] == 12.0              # (112 − 100) ÷ 100 × 100
    assert y["initial_from_prior_fy_statement"] is True
    kinds = [e["kind"] for e in y["timeline"]]
    assert kinds == ["initial", "revision"]
    assert y["revisions"][0]["rate"] == 10.0                      # (110 − 100) ÷ |100| × 100
    assert y["actual"]["values"]["operating_profit"] == 112 * OKU


def test_upward_majority_without_revisions_is_not_conservative(db_conn):
    fx.store(db_conn, typed_company([(100, [], 112)] * 3))
    assert classify(db_conn)["type"] == "volatile"


def test_fewer_than_three_years_is_insufficient(db_conn):
    fx.store(db_conn, typed_company([(100, [110], 112)] * 2))
    t = classify(db_conn)
    assert t["type"] == "insufficient" and t["n"] == 2
    assert "判定に必要な期数が不足" in t["text"] and "2期中2期" in t["text"]


def test_at_most_five_years_are_used(db_conn):
    fx.store(db_conn, typed_company([(100, [90], 88)] * 2 + [(100, [110], 112)] * 5))
    t = classify(db_conn)
    assert t["n"] == 5 and t["type"] == "conservative_upward"


def test_unchanged_forecast_is_not_a_revision():
    obs = [{"fy_end": "2025-03-31", "source": "current_year", "disclosed_date": d, "disclosed_time": "",
            "disclosure_no": n, "doc_type": "x", "doc_kind": "statement",
            "values": {"net_sales": None, "operating_profit": v, "profit": None, "eps": None, "dividend": None}}
           for d, n, v in (("2024-05-10", "a", 100.0), ("2024-08-05", "b", 100.0), ("2024-11-05", "c", 95.0))]
    h = fm.revision_history([], obs)[0]
    assert [e["kind"] for e in h["timeline"]] == ["initial", "unchanged", "revision"]
    assert h["revisions"][0]["rate"] == pytest.approx(-5.0)


# --------------------------------------------------------------------------- 訂正検知 (4.6)

def _row(op="10000000000", eq="200000000000"):
    fs, fe = fx.fy_dates(2026)
    r = stmt(CODE, "20260510000001", "2026-05-10", "FY", fs, fe, sales=1000, op=100, np_=60, eq=2000)
    r["OP"], r["Eq"] = op, eq
    return r


def test_same_disclosure_no_with_changed_content_is_logged(db_conn):
    responses = {"rows": [_row()]}
    transport = FakeTransport(handler=lambda req: HttpResponse(200, {"data": responses["rows"]}))
    client = make_jq_client(transport)

    first = fins.ensure_fins(db_conn, client, CODE)
    assert first["state"] == "fetched" and first["has_fins"] is True
    assert transport.requests[0].url.endswith(ENDPOINT_FINS_SUMMARY)
    assert transport.requests[0].params == {"code": CODE}

    second = fins.ensure_fins(db_conn, client, CODE)               # stored and fresh: no API call
    assert second["state"] == "stored" and len(transport.requests) == 1

    same = fins.ensure_fins(db_conn, client, CODE, force=True)     # identical content: no log entry
    assert same["revisions_detected"] == 0
    assert db_conn.execute("SELECT COUNT(*) FROM fins_revision_log").fetchone()[0] == 0

    responses["rows"] = [_row(op="12000000000")]                   # 開示番号は同じ、内容が訂正された
    out = fins.ensure_fins(db_conn, client, CODE, force=True)
    assert out["state"] == "fetched" and out["revisions_detected"] == 1
    row = db_conn.execute("SELECT operating_profit, raw_json FROM fins_summary WHERE disclosure_no='20260510000001'"
                          ).fetchone()
    assert row["operating_profit"] == 12_000_000_000               # new values kept
    assert json.loads(row["raw_json"])["OP"] == "12000000000"
    log = db_conn.execute("SELECT * FROM fins_revision_log").fetchall()
    assert len(log) == 1
    changes = json.loads(log[0]["changes_json"])
    assert changes == [{"field": "OP", "column": "operating_profit", "old": "10000000000", "new": "12000000000"}]
    assert json.loads(log[0]["old_raw_json"])["OP"] == "10000000000"   # old values appended
    assert log[0]["old_sha256"] != log[0]["new_sha256"]

    payload = financials.financials_payload(db_conn, CODE)
    assert payload["correction_log"][0]["changes"][0]["old"] == "10000000000"


def test_refetch_after_seven_days(db_conn):
    transport = FakeTransport(handler=lambda req: HttpResponse(200, {"data": [_row()]}))
    client = make_jq_client(transport)
    fins.ensure_fins(db_conn, client, CODE)
    later = datetime.now(timezone.utc) + timedelta(days=6)
    assert fins.ensure_fins(db_conn, client, CODE, now=later)["state"] == "stored"
    later = datetime.now(timezone.utc) + timedelta(days=8)
    assert fins.ensure_fins(db_conn, client, CODE, now=later)["state"] == "fetched"
    assert len(transport.requests) == 2


def test_fetch_failure_is_reported_and_stored_data_kept(db_conn):
    fx.store(db_conn, [_row()])
    transport = FakeTransport(handler=lambda req: HttpResponse(403, {"message": "plan"}))
    out = fins.ensure_fins(db_conn, make_jq_client(transport), CODE, force=True)
    assert out["state"] == "failed" and out["has_fins"] is True and "403" in out["error_reason"]


def test_by_date_fetch_and_nightly_queue(db_conn):
    from stockapp import bars

    other = dict(_row(), Code="20000", DiscNo="X2")
    transport = FakeTransport(handler=lambda req: HttpResponse(200, {"data": [_row(), other]}))
    client = make_jq_client(transport)
    out = fins.fetch_fins_by_date(db_conn, client, "2026-05-10")
    assert out["result"] == "ok" and out["inserted"] == 2
    assert transport.requests[0].params == {"date": "2026-05-10"}
    db_conn.executemany("INSERT INTO trading_calendar(date, holiday_division, is_business_day, fetched_at)"
                        " VALUES (?, '1', 1, 'x')", [("2026-11-19",), ("2026-11-20",)])
    assert bars.queue_fins_by_date(db_conn, "2026-11-20") == ["2026-11-19", "2026-11-20"]
    done = bars.run_queue(db_conn, client)
    assert {d["job"] for d in done} == {"fins_by_date"} and {d["result"] for d in done} == {"ok"}
