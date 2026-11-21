"""要件 3.7 確認ポイント（財務6種）: each rule fires on a triggering fixture and
not on a non-triggering one, with the boundary values (受け入れ条件⑤)."""
from __future__ import annotations

import pytest

import fins_fixtures as fx
from fins_fixtures import stmt
from stockapp import financials

CODE = "40010"
F25 = fx.fy_dates(2025)
F26 = fx.fy_dates(2026)


def rules_fired(conn) -> set[str]:
    return {a["rule"] for a in financials.financials_payload(conn, CODE)["alerts"]["items"]}


def alert_items(conn):
    return financials.financials_payload(conn, CODE)["alerts"]["items"]


# 1. 増収減益 -------------------------------------------------------------------

@pytest.mark.parametrize("cur_op,fires", [(9, True), (10, False), (10.5, False)])
def test_revenue_up_operating_profit_down(db_conn, cur_op, fires):
    fx.store(db_conn, [
        stmt(CODE, "A1", "2024-11-05", "2Q", *F25, sales=100, op=10, np_=6),
        stmt(CODE, "A2", "2025-11-05", "2Q", *F26, sales=110, op=cur_op, np_=6),
    ])
    assert rules_fired(db_conn) == ({"revenue_up_profit_down"} if fires else set())
    if fires:
        a = alert_items(db_conn)[0]
        assert a["text"] == ("第2四半期累計の売上高は前年同期比+10.0%、営業利益は−10.0%（2026年3月期）"
                             "（基準日：2025-11-05）")
        assert a["as_of"] == "2025-11-05" and a["check"].startswith("一般に")


def test_revenue_down_profit_down_is_not_flagged(db_conn):
    fx.store(db_conn, [stmt(CODE, "A1", "2024-11-05", "2Q", *F25, sales=100, op=10, np_=6),
                       stmt(CODE, "A2", "2025-11-05", "2Q", *F26, sales=99, op=9, np_=6)])
    assert rules_fired(db_conn) == set()


# 2. 営業CF÷純利益 < 0.5 --------------------------------------------------------

@pytest.mark.parametrize("cfo,np_,fires", [(49, 100, True), (50, 100, False), (120, 100, False), (10, -5, False)])
def test_operating_cf_to_profit(db_conn, cfo, np_, fires):
    fx.store(db_conn, [stmt(CODE, "B1", "2025-05-09", "FY", *F25, sales=1000, op=150, np_=np_, cfo=cfo, cfi=-10,
                            cff=-5)])
    assert rules_fired(db_conn) == ({"ocf_to_profit_low"} if fires else set())
    if fires:
        assert alert_items(db_conn)[0]["text"] == "2025年3月期の営業CFは純利益の0.49倍（基準日：2025-05-09）"


# 3. 進捗率の乖離 ±15pt（暫定） -----------------------------------------------------

def _progress_rows(cur_op):
    rows = []
    for y, q2 in ((2023, 50), (2024, 40), (2025, 45)):
        fs, fe = fx.fy_dates(y)
        rows.append(stmt(CODE, f"P{y}-2", f"{y - 1}-11-05", "2Q", fs, fe, sales=500, op=q2, np_=20, f_op=100))
        rows.append(stmt(CODE, f"P{y}-4", f"{y}-05-10", "FY", fs, fe, sales=1000, op=100, np_=50))
    rows.append(stmt(CODE, "C2", "2025-11-05", "2Q", *F26, sales=500, op=cur_op, np_=20, f_op=100))
    return rows


@pytest.mark.parametrize("cur_op,fires", [(60, True), (30, True), (59.9, False), (30.1, False)])
def test_progress_deviation(db_conn, cur_op, fires):
    fx.store(db_conn, _progress_rows(cur_op))
    assert rules_fired(db_conn) == ({"progress_deviation"} if fires else set())
    if fires:
        a = alert_items(db_conn)[0]
        assert a["provisional"] is True and "暫定値" in a["text"]
        assert "平均45.0%との差" in a["text"]


# 4. 配当性向 100% 超 -----------------------------------------------------------

@pytest.mark.parametrize("div,fires", [(101, True), (100, False), (30, False)])
def test_payout_ratio_actual(db_conn, div, fires):
    fx.store(db_conn, [stmt(CODE, "D1", "2025-05-09", "FY", *F25, sales=1000, op=100, np_=50, eps=100,
                            div_ann=div)])
    assert rules_fired(db_conn) == ({"payout_over_100"} if fires else set())
    if fires:
        assert alert_items(db_conn)[0]["text"].startswith("実績配当性向101.0%（配当が1株利益を上回る）")


@pytest.mark.parametrize("div,fires", [(101, True), (100, False)])
def test_payout_ratio_forecast(db_conn, div, fires):
    fx.store(db_conn, [stmt(CODE, "D2", "2025-08-05", "1Q", *F26, sales=250, op=25, np_=12, f_op=100,
                            f_eps=100, f_div=div)])
    assert rules_fired(db_conn) == ({"payout_over_100"} if fires else set())
    if fires:
        assert alert_items(db_conn)[0]["text"].startswith("予想配当性向101.0%（配当が1株利益を上回る）")


# 5. 自己資本比率の低下 5pt 以上 -----------------------------------------------------

@pytest.mark.parametrize("cur,fires", [(0.45, True), (0.40, True), (0.451, False), (0.52, False)])
def test_equity_ratio_drop(db_conn, cur, fires):
    fx.store(db_conn, [stmt(CODE, "E1", "2024-11-05", "2Q", *F25, sales=100, op=10, np_=6, eqar=0.50),
                       stmt(CODE, "E2", "2025-11-05", "2Q", *F26, sales=100, op=10, np_=6, eqar=cur)])
    assert rules_fired(db_conn) == ({"equity_ratio_drop"} if fires else set())
    if fires and cur == 0.45:
        assert alert_items(db_conn)[0]["text"] == (
            "自己資本比率は前年同期の50.0%から45.0%へ（−5.0ポイント）（第2四半期末・2026年3月期）（基準日：2025-11-05）")


# 6. 予想の大幅修正 |修正率| >= 10% ---------------------------------------------------

@pytest.mark.parametrize("new_op,fires", [(110, True), (90, True), (109, False), (91, False)])
def test_forecast_revision(db_conn, new_op, fires):
    fx.store(db_conn, [
        stmt(CODE, "F1", "2025-08-05", "1Q", *F26, sales=250, op=25, np_=12, f_sales=1000, f_op=100, f_np=50),
        fx.revision(CODE, "F2", "2025-10-20", *F26, f_sales=1000, f_op=new_op, f_np=50),
    ])
    assert rules_fired(db_conn) == ({"forecast_revision"} if fires else set())
    if fires:
        sign = "+" if new_op > 100 else "−"
        assert alert_items(db_conn)[0]["text"] == (
            f"2025-10-20開示で通期営業利益予想を{sign}10.0%修正（2026年3月期）（基準日：2025-10-20）")


def test_revisions_of_completed_years_are_not_current_alerts(db_conn):
    f25s, f25e = F25
    fx.store(db_conn, [
        stmt(CODE, "G1", "2024-08-05", "1Q", f25s, f25e, sales=250, op=25, np_=12, f_op=100),
        fx.revision(CODE, "G2", "2024-10-20", f25s, f25e, f_op=150),            # +50%, but FY2025-03 is closed
        stmt(CODE, "G3", "2025-05-09", "FY", f25s, f25e, sales=1000, op=140, np_=50,
             nx_start=F26[0], nx_end=F26[1], nx_op=120),
    ])
    assert "forecast_revision" not in rules_fired(db_conn)


def test_no_alerts_reports_none_with_date(db_conn):
    fx.store(db_conn, [stmt(CODE, "H1", "2025-05-09", "FY", *F25, sales=1000, op=100, np_=50, cfo=80)])
    al = financials.financials_payload(db_conn, CODE)["alerts"]
    assert al["items"] == [] and al["none_text"] == "該当なし（判定基準日：2025-05-09）"
    assert al["thresholds"]["progress_deviation_pt"] == 15
