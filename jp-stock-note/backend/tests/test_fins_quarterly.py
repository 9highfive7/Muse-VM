"""要件 5.3 四半期単独値: cumulative -> standalone conversion, Q4 = FY − 3Q
with the special-factors note, fiscal-year-end change, accounting-standard
change and missing quarters (受け入れ条件①②)."""
from __future__ import annotations

import pytest

import fins_fixtures as fx
from fins_fixtures import OKU, stmt
from stockapp import financials, fundamentals as fm

CODE = "10010"


def _rows_for_year(code, fs, fe, cum, *, prefix="Q", std=None, disc=None):
    """cum: {period: (sales, op, np)} in 億円."""
    rows = []
    for i, (pt, (s, o, n)) in enumerate(cum.items(), 1):
        std_pt = (std or {}).get(pt, "JP")
        rows.append(stmt(code, f"{prefix}{i}", (disc or {}).get(pt, fe), pt, fs, fe, std=std_pt,
                         sales=s, op=o, np_=n))
    return rows


def standalone(conn, code):
    return fm.quarterly_standalone(fm.statements(fm.load_records(conn, code)))


def test_cumulative_to_standalone_hand_computed(db_conn):
    # 1Q 100/10/6, 2Q 220/25/15, 3Q 330/35/21, FY 480/52/30 (cumulative, 億円)
    fx.store(db_conn, _rows_for_year(CODE, "2024-04-01", "2025-03-31", {
        "1Q": (100, 10, 6), "2Q": (220, 25, 15), "3Q": (330, 35, 21), "FY": (480, 52, 30)}))
    q = standalone(db_conn, CODE)
    assert [r["quarter"] for r in q] == [1, 2, 3, 4]
    expected = [(100, 10, 6), (120, 15, 9), (110, 10, 6), (150, 17, 9)]   # Q4 = FY − 3Q
    for row, (s, o, n) in zip(q, expected):
        assert row["values"] == {"net_sales": s * OKU, "operating_profit": o * OKU, "profit": n * OKU}
        assert all(v is None for v in row["reasons"].values())
    # 4 standalone quarters add up to the full-year actual (受け入れ条件①)
    for item, fy in (("net_sales", 480), ("operating_profit", 52), ("profit", 30)):
        assert sum(r["values"][item] for r in q) == fy * OKU
    # Q4 carries the year-end special-factors note flag; other quarters do not
    assert [r["special_factors_note"] for r in q] == [False, False, False, True]


@pytest.mark.parametrize("end_month", [3, 12, 6])   # 3月期・12月期・その他（6月期）
def test_four_quarters_sum_to_full_year_for_each_fiscal_month(db_conn, end_month):
    fs, fe = fx.fy_dates(2025, end_month)
    fx.store(db_conn, _rows_for_year(CODE, fs, fe, {
        "1Q": (231, 17, 11), "2Q": (502, 41, 23), "3Q": (777, 60, 38), "FY": (1013, 77, 49)}))
    q = standalone(db_conn, CODE)
    assert len(q) == 4 and q[0]["fy_start"] == fs
    for item, fy in (("net_sales", 1013), ("operating_profit", 77), ("profit", 49)):
        assert sum(r["values"][item] for r in q) == fy * OKU
    assert q[3]["values"]["net_sales"] == (1013 - 777) * OKU


def test_fiscal_year_end_change(db_conn):
    """3月期 -> 12月期: transitional year 2024-04-01〜2024-12-31 (9 months:
    1Q, 2Q, FY). Its FY is not converted; the next year's Q1 is not
    differenced against the transitional year."""
    rows = _rows_for_year(CODE, "2023-04-01", "2024-03-31", {
        "1Q": (100, 10, 6), "2Q": (200, 20, 12), "3Q": (300, 30, 18), "FY": (400, 40, 24)}, prefix="A")
    rows += _rows_for_year(CODE, "2024-04-01", "2024-12-31", {
        "1Q": (110, 11, 7), "2Q": (220, 22, 14), "FY": (330, 33, 21)}, prefix="B")
    rows += _rows_for_year(CODE, "2025-01-01", "2025-12-31", {"1Q": (120, 12, 8)}, prefix="C")
    fx.store(db_conn, rows)
    q = standalone(db_conn, CODE)
    trans = [r for r in q if r["fy_end"] == "2024-12-31"]
    assert [r["period_type"] for r in trans] == ["1Q", "2Q", "FY"]
    assert trans[0]["values"]["net_sales"] == 110 * OKU
    assert trans[1]["values"]["net_sales"] == 110 * OKU                 # 220 − 110
    fy = trans[2]
    assert fy["irregular_year"] is True
    assert fy["values"] == {"net_sales": None, "operating_profit": None, "profit": None}
    assert set(fy["reasons"].values()) == {"irregular_year"}
    nxt = [r for r in q if r["fy_end"] == "2025-12-31"][0]
    assert nxt["values"]["net_sales"] == 120 * OKU                      # Q1 = 1Q cumulative, no subtraction
    # YoY of 2025 1Q vs the transitional year: not comparable -> 算出不可 with reason
    payload = financials.financials_payload(db_conn, CODE)
    assert payload["yoy"]["state"] == "unavailable"
    assert payload["yoy"]["reason"] == "前年度が決算期変更による変則決算期のため比較できない"
    fy_row = next(r for r in payload["quarterly"]["rows"] if r["fy_end"] == "2024-12-31" and r["quarter"] == 4)
    assert fy_row["reasons"]["net_sales"] == "決算期変更による変則決算期のため算出不可"
    annual = {r["fy_end"]: r for r in payload["annual"]["rows"]}
    assert annual["2024-12-31"]["irregular_year"] is True and annual["2024-12-31"]["months"] == 9


def test_accounting_standard_change_is_not_differenced(db_conn):
    fx.store(db_conn, _rows_for_year(CODE, "2024-04-01", "2025-03-31", {
        "1Q": (100, 10, 6), "2Q": (220, 25, 15), "3Q": (330, 35, 21), "FY": (480, 52, 30)},
        std={"1Q": "JP", "2Q": "IFRS", "3Q": "IFRS", "FY": "IFRS"}))
    q = standalone(db_conn, CODE)
    assert q[0]["values"]["net_sales"] == 100 * OKU
    assert q[1]["values"]["net_sales"] is None and q[1]["reasons"]["net_sales"] == "standard_changed"
    assert q[2]["values"]["net_sales"] == 110 * OKU          # 2Q and 3Q are both IFRS
    payload = financials.financials_payload(db_conn, CODE)
    row = payload["quarterly"]["rows"][1]
    assert row["reasons"]["operating_profit"] == "会計基準が異なるため算出不可"


def test_missing_previous_quarter_and_last_eight(db_conn):
    rows = fx.march_company(CODE)
    rows = [r for r in rows if not (r["CurFYEn"] == "2026-03-31" and r["CurPerType"] == "2Q")]
    fx.store(db_conn, rows)
    payload = financials.financials_payload(db_conn, CODE)
    q = payload["quarterly"]["rows"]
    assert len(q) == 8                                       # last 8 quarters only
    assert q[-1]["label"] == "2027年3月期 Q2"
    q3 = next(r for r in q if r["fy_end"] == "2026-03-31" and r["quarter"] == 3)
    assert q3["values"]["net_sales"] is None
    assert q3["reasons"]["net_sales"] == "前四半期の累計が未取得のため算出不可"
    assert any("差分" in n for n in payload["quarterly"]["notes"])
    assert any("特別要因" in n for n in payload["quarterly"]["notes"])
