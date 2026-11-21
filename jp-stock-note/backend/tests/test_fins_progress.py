"""要件 5.3 進捗率: current progress vs the forecast at that disclosure, the
past-3-years same-quarter pattern, deviation in points, 比較不足."""
from __future__ import annotations

import pytest

import fins_fixtures as fx
from fins_fixtures import stmt
from stockapp import financials, fundamentals as fm

CODE = "10030"


def progress_rows(cur_op_2q: float = 60, past=((2024, 50), (2025, 40), (2026, 45)), extra_old=True,
                  forecast_op: float = 100):
    """Past years: FY op 100 and 2Q cumulative op = the given value (=> pattern %).
    Current FY (2027-03): 2Q cumulative op ``cur_op_2q`` vs forecast ``forecast_op``."""
    rows = []
    years = list(past)
    if extra_old:
        years = [(2023, 90)] + years          # 4th year back: must NOT be used
    for y, q2 in years:
        fs, fe = fx.fy_dates(y)
        rows.append(stmt(CODE, f"P{y}-2", f"{y - 1}-11-05", "2Q", fs, fe, sales=500, op=q2, np_=q2 / 2,
                         f_sales=1000, f_op=100, f_np=50))
        rows.append(stmt(CODE, f"P{y}-4", f"{y}-05-10", "FY", fs, fe, sales=1000, op=100, np_=50))
    fs, fe = fx.fy_dates(2027)
    rows.append(stmt(CODE, "C2027-2", "2026-11-05", "2Q", fs, fe, sales=520, op=cur_op_2q, np_=30,
                     f_sales=1000, f_op=forecast_op, f_np=50))
    return rows


def prog(conn):
    return financials.financials_payload(conn, CODE)["progress"]


def test_progress_vs_past_three_year_pattern(db_conn):
    fx.store(db_conn, progress_rows())
    p = prog(db_conn)
    assert p["state"] == "ok" and p["period_type"] == "2Q" and p["primary_item"] == "operating_profit"
    op = p["items"]["operating_profit"]
    assert op["actual_pct"] == 60.0                          # 60 ÷ 100 × 100
    assert [y["fy_end"] for y in op["pattern_years"]] == ["2026-03-31", "2025-03-31", "2024-03-31"]
    assert [y["pct"] for y in op["pattern_years"]] == [45.0, 40.0, 50.0]
    assert op["pattern_avg_pct"] == 45.0                      # (45 + 40 + 50) ÷ 3, 2023 (90%) not used
    assert (op["pattern_min_pct"], op["pattern_max_pct"]) == (40.0, 50.0)
    assert op["deviation_pt"] == 15.0                         # 60 − 45
    assert op["beyond_threshold"] is True
    assert p["provisional"] is True and p["threshold_pt"] == 15
    # 補助：当時の予想比 (2Q cumulative ÷ forecast at that 2Q disclosure)
    assert [y["pct_vs_forecast_then"] for y in op["pattern_years"]] == [45.0, 40.0, 50.0]
    card = financials.business_cards(db_conn, CODE)["cards"][0]
    text = next(s["text"] for s in card["sentences"] if s["key"] == "progress")
    assert "進捗率は通期営業利益予想の60.0%" in text and "40.0〜50.0%" in text and "+15.0ポイント" in text
    assert "基準日：2026-11-05" in text


def test_progress_uses_forecast_known_at_that_disclosure(db_conn):
    rows = progress_rows()
    fs, fe = fx.fy_dates(2027)
    rows.append(fx.revision(CODE, "R1", "2026-12-15", fs, fe, f_op=120))   # later revision
    fx.store(db_conn, rows)
    op = prog(db_conn)["items"]["operating_profit"]
    assert op["forecast"] == 100 * fx.OKU and op["actual_pct"] == 60.0


def test_comparison_insufficient_with_two_past_years(db_conn):
    fx.store(db_conn, progress_rows(past=((2025, 40), (2026, 45)), extra_old=False))
    op = prog(db_conn)["items"]["operating_profit"]
    assert op["actual_pct"] == 60.0
    assert op["pattern_state"] == "insufficient" and op["pattern_n_valid"] == 2
    assert op["pattern_avg_pct"] is None and op["deviation_pt"] is None
    card = financials.business_cards(db_conn, CODE)["cards"][0]
    text = next(s["text"] for s in card["sentences"] if s["key"] == "progress")
    assert "比較不足" in text


def test_forecast_nonpositive_is_not_computed(db_conn):
    fx.store(db_conn, progress_rows(forecast_op=-10))
    op = prog(db_conn)["items"]["operating_profit"]
    assert op["actual_pct"] is None and op["actual_reason"] == "予想が赤字またはゼロ"


@pytest.mark.parametrize("cur,dev", [(60, 15.0), (30, -15.0), (59.9, 14.9)])
def test_deviation_points(db_conn, cur, dev):
    fx.store(db_conn, progress_rows(cur_op_2q=cur))
    op = prog(db_conn)["items"]["operating_profit"]
    assert op["deviation_pt"] == pytest.approx(dev)
    assert op["beyond_threshold"] is (abs(dev) >= 15)


def test_latest_fy_disclosure_is_not_applicable(db_conn):
    fx.store(db_conn, [r for r in progress_rows() if r["DiscNo"] != "C2027-2"])
    assert prog(db_conn)["state"] == "not_applicable"
    raw = fm.progress(fm.statements(fm.load_records(db_conn, CODE)), [])
    assert raw["state"] == "not_applicable"
