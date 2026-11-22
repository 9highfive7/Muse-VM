"""「テクニカルで見る」: 今の評価（各指標のルール判定）、単純バックテスト（勝率・回数・
ノンオーバーラップ）、サンプル不足の表示、API の応答形式。

This block is the documented exception to 要件 6.1 (「買いです／売りです」「勝率」
are shown at the user's explicit request). The wording checks here therefore
use ``find_banned(..., allow=TECH_VIEW_ALLOWED)``: only 「勝率」 is tolerated.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from conftest import store_synthetic
from stockapp import indicators as ind
from stockapp import technical_view as tv
from stockapp.db import connect
from stockapp.technicals import Analysis
from stockapp.wording import TECH_VIEW_ALLOWED, find_banned, num
from test_cross_and_chart_api import expected_crosses


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


def analysis_from_closes(closes: list[float], start: str = "2024-01-01") -> Analysis:
    dates = [d.date().isoformat() for d in pd.bdate_range(start, periods=len(closes))]
    bars = pd.DataFrame({"date": dates, "open": closes, "high": closes, "low": closes, "close": closes,
                         "volume": 1000.0, "raw_close": closes, "adj_factor": 1.0,
                         "upper_limit": False, "lower_limit": False})
    frame = ind.compute_frame(bars)
    return Analysis("99990", bars, frame, pd.DataFrame(columns=["date", "close"]),
                    pd.DataFrame(columns=["date", "close", "volume", "topix"]))


# --------------------------------------------------------------------------- rules (今の評価)

@pytest.mark.parametrize("value,side", [
    (12.0, "buy"), (29.9, "buy"), (30.0, "buy"), (30.04, "buy"),     # 30.04 is shown as 30.0
    (30.1, "neutral"), (50.0, "neutral"), (69.9, "neutral"),
    (70.0, "sell"), (88.0, "sell"), (float("nan"), None), (None, None),
])
def test_rsi_rule(value, side):
    assert tv.rsi_side(value) == side


@pytest.mark.parametrize("close,side", [(90.0, "buy"), (95.0, "buy"), (100.0, "neutral"), (105.0, "sell"),
                                        (110.0, "sell"), (float("nan"), None)])
def test_bollinger_rule(close, side):
    assert tv.bollinger_side(close, 95.0, 105.0) == side


@pytest.mark.parametrize("kind,gap,side", [
    ("golden", None, "buy"), ("dead", None, "sell"), (None, None, "neutral"),     # 移動平均クロス
    ("golden", 1.5, "buy"), ("dead", -0.2, "sell"),                              # MACD: 抜けた側の圏内
    ("golden", 0.0, "neutral"), ("golden", -0.1, "neutral"), ("dead", 0.3, "neutral"), (None, 2.0, "neutral"),
])
def test_cross_rule(kind, gap, side):
    assert tv.cross_side(kind, gap) == side


def test_rsi_evaluation_states_value_and_rule():
    falling = [200.0 - i for i in range(40)]              # every move down -> RSI 0
    ev = tv.evaluate(analysis_from_closes(falling), "rsi")
    assert ev["side"] == "buy"
    assert ev["text"] == "RSIだと買いです"
    assert ev["basis"].startswith("RSI 0.0。30以下を買い、70以上を売り")
    assert "一般的なルール" in ev["basis"] and "基準日：" in ev["basis"]
    rising = [100.0 + i for i in range(40)]
    assert tv.evaluate(analysis_from_closes(rising), "rsi")["text"] == "RSIだと売りです"
    flat_mix = [100.0 + (1 if i % 2 else -1) for i in range(40)]  # equal ups and downs -> 50
    ev = tv.evaluate(analysis_from_closes(flat_mix), "rsi")
    assert ev["side"] == "neutral" and ev["text"] == "RSIだと中立（様子見）です"


def test_bollinger_evaluation_states_close_and_bands():
    closes = [100.0 + (0.5 if i % 2 else -0.5) for i in range(30)] + [90.0]   # sharp drop below −2σ
    a = analysis_from_closes(closes)
    ev = tv.evaluate(a, "bollinger")
    assert ev["side"] == "buy" and ev["text"] == "ボリンジャーバンドだと買いです"
    last = a.frame.iloc[-1]
    assert ev["basis"].startswith(f"終値 {num(90.0)}円、+2σ {num(last['bb_upper'])}円、中心 {num(last['bb_mid'])}円、"
                                  f"−2σ {num(last['bb_lower'])}円。")
    assert "−2σ以下なら買い、+2σ以上なら売り" in ev["basis"]


def test_ma_cross_evaluation_states_cross_date_and_elapsed_days():
    closes = [200.0 - i for i in range(100)] + [101.0 + 3 * i for i in range(40)]   # falls, then rises -> GC
    a = analysis_from_closes(closes)
    events = ind.crosses(a.frame["sma25"], a.frame["sma75"])
    i, kind = events[-1]
    assert kind == "golden"
    ev = tv.evaluate(a, "ma_cross")
    assert ev["side"] == "buy" and ev["text"] == "移動平均クロスだと買いです"
    days = len(closes) - 1 - i
    assert ev["values"]["last_cross"] == {"date": a.frame["date"].iloc[i], "kind": "golden", "business_days_ago": days}
    assert f"直近のクロスは{a.frame['date'].iloc[i]}に25日線が75日線を上抜け（ゴールデンクロス、{days}営業日前）" in ev["basis"]


def test_ma_cross_without_any_cross_is_neutral():
    a = analysis_from_closes([100.0 + i for i in range(120)])   # 25 above 75 from the start: no cross
    ev = tv.evaluate(a, "ma_cross")
    assert ev["side"] == "neutral" and ev["values"]["last_cross"] is None
    assert "クロスなし" in ev["basis"]


def test_macd_evaluation_states_values_and_last_cross():
    closes = [200.0 - i for i in range(80)] + [121.0 + 2 * i for i in range(30)]   # turn up -> MACD crosses up
    a = analysis_from_closes(closes)
    ev = tv.evaluate(a, "macd")
    assert ev["side"] == "buy" and ev["text"] == "MACDだと買いです"
    last = a.frame.iloc[-1]
    i, kind = ind.crosses(a.frame["macd"], a.frame["macd_signal"])[-1]
    assert kind == "golden"
    assert ev["basis"].startswith(f"MACD {num(last['macd'], 2)}、シグナル {num(last['macd_signal'], 2)}"
                                  f"（MACD−シグナル {num(last['macd'] - last['macd_signal'], 2)}）。"
                                  f"直近のクロスは{a.frame['date'].iloc[i]}にMACDがシグナルを上抜け")
    assert ev["values"]["last_cross"]["business_days_ago"] == len(closes) - 1 - i


def test_unavailable_when_too_few_bars():
    a = analysis_from_closes([100.0 + i for i in range(10)])
    for indicator in tv.INDICATORS:
        ev = tv.evaluate(a, indicator)
        assert ev["side"] == "unavailable"
        assert "今の評価は出せません" in ev["text"]


# --------------------------------------------------------------------------- signal days

def test_signal_events_level_indicators_list_every_day_in_zone():
    frame = pd.DataFrame({
        "rsi": [np.nan, 50, 30.0, 25, 31, 70.0, 75, 69.9, 29.9],
        "close": [100, 100, 94, 95, 100, 106, 105, 100, 80.0],
        "bb_lower": [np.nan, 95, 95, 95, 95, 95, 95, 95, 95.0],
        "bb_upper": [np.nan, 105, 105, 105, 105, 105, 105, 105, 105.0],
    })
    assert tv.signal_events(frame, "rsi") == {"buy": [2, 3, 8], "sell": [5, 6]}
    assert tv.signal_events(frame, "bollinger") == {"buy": [2, 3, 8], "sell": [5, 6]}


def test_signal_events_cross_indicators_use_cross_days_only():
    a = [np.nan, 1.0, 2.0, 3.0, 2.0, 1.0, 1.0, 3.0]
    b = [np.nan, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0]
    # t=3: 2<=2 & 3>2 -> up; t=5: 2>=2 & 1<2 -> down; t=7: 1<=2 & 3>2 -> up
    frame = pd.DataFrame({"macd": a, "macd_signal": b, "sma25": a, "sma75": b})
    assert tv.signal_events(frame, "macd") == {"buy": [3, 7], "sell": [5]}
    assert tv.signal_events(frame, "ma_cross") == {"buy": [3, 7], "sell": [5]}


def test_non_overlapping_skips_signals_inside_the_holding_period():
    assert tv.non_overlapping([0, 1, 2, 3, 6, 7, 8, 10], 3) == [0, 3, 6, 10]
    assert tv.non_overlapping([0, 1, 2, 3], 1) == [0, 1, 2, 3]
    assert tv.non_overlapping([], 10) == []


# --------------------------------------------------------------------------- backtest (hand-calculated fixture)

CLOSES = np.array([100, 102, 101, 99, 103, 104, 100, 98, 97, 101, 105, 104], dtype=float)
DATES = [f"2024-01-{d:02d}" for d in range(1, 13)]


def test_backtest_buy_side_matches_hand_calculation():
    # buy signal days 0,1,3,6,7,8,10; holding 3 days -> counted 0,3,6,10 (1,7,8 fall inside a holding period)
    #   0: C3/C0  =  99/100 -1 = −1.0000% -> loss
    #   3: C6/C3  = 100/99  -1 = +1.0101% -> win
    #   6: C9/C6  = 101/100 -1 = +1.0000% -> win
    #  10: exit day 13 is beyond the data -> pending
    r = tv.backtest_side(CLOSES, DATES, [0, 1, 3, 6, 7, 8, 10], "buy", 3)
    assert (r.signals, r.samples, r.pending, r.wins, r.losses, r.draws) == (4, 3, 1, 2, 1, 0)
    assert r.win_rate_pct == pytest.approx(200 / 3)
    assert r.avg_return_pct == pytest.approx((-1.0 + (100 / 99 - 1) * 100 + 1.0) / 3)
    assert [e["date"] for e in r.entries] == ["2024-01-01", "2024-01-04", "2024-01-07"]
    assert [e["result"] for e in r.entries] == ["loss", "win", "win"]
    assert r.small_sample


def test_backtest_sell_side_counts_falls_as_wins():
    # sell days 2,4,5; holding 3 -> counted 2,5
    #   2: C5/C2 = 104/101 -1 = +2.97% -> price rose -> loss
    #   5: C8/C5 =  97/104 -1 = −6.73% -> price fell -> win
    r = tv.backtest_side(CLOSES, DATES, [2, 4, 5], "sell", 3)
    assert (r.signals, r.samples, r.wins, r.losses) == (2, 2, 1, 1)
    assert r.win_rate_pct == pytest.approx(50.0)
    assert r.avg_return_pct == pytest.approx(((104 / 101 - 1) + (97 / 104 - 1)) / 2 * 100)


def test_backtest_unchanged_price_is_a_draw_not_a_win():
    r = tv.backtest_side(np.array([100.0, 100.0, 100.0]), DATES[:3], [0], "buy", 2)
    assert (r.samples, r.wins, r.losses, r.draws, r.win_rate_pct) == (1, 0, 0, 1, 0.0)


def test_backtest_ten_samples_is_not_small():
    closes = np.arange(100.0, 130.0)
    dates = [f"d{i}" for i in range(30)]
    r = tv.backtest_side(closes, dates, list(range(20)), "buy", 2)    # counted 0,2,...,18
    assert (r.samples, r.wins, r.win_rate_pct) == (10, 10, 100.0)
    assert not r.small_sample
    r9 = tv.backtest_side(closes, dates, list(range(18)), "buy", 2)
    assert r9.samples == 9 and r9.small_sample


def test_baseline_share_of_up_days():
    b = tv.baseline(CLOSES, 3)
    # C(t+3)>C(t) for t=0..8: 99>100 n, 103>102 y, 104>101 y, 100>99 y, 98>103 n, 97>104 n, 101>100 y, 105>98 y, 104>97 y
    assert (b["days"], b["up_days"]) == (9, 6)
    assert b["up_pct"] == pytest.approx(200 / 3)


def test_period_text():
    assert tv.period_text("2021-12-01", "2026-11-20", 1230) == "対象期間：2021-12-01〜2026-11-20（約5年間・1,230営業日）"
    assert tv.period_text("2025-03-03", "2026-11-20", 425) == "対象期間：2025-03-03〜2026-11-20（約1.7年間・425営業日）"
    assert tv.period_text("2026-05-01", "2026-11-20", 140) == "対象期間：2026-05-01〜2026-11-20（約7か月間・140営業日）"


# --------------------------------------------------------------------------- headline / small-sample display

def _res(side, samples, wins, horizon=tv.MAIN_HORIZON):
    return tv.SideResult(side, horizon, samples, samples, 0, wins, samples - wins, 0,
                         None if samples == 0 else wins / samples * 100, 0.5 if samples else None, [])


def test_headline_marks_small_samples():
    h = tv.headline("buy", {"buy": [_res("buy", 4, 3)], "sell": []}, "対象期間：X")
    assert h["kind"] == "rate"
    assert h["text"] == "過去の同テクニカル分析のバックテスト結果だと勝率75%です"
    assert h["label"] == "勝率（買いの判定・10営業日後）"
    assert h["small_sample"] is True and h["small_sample_text"] == "サンプルが少なく参考値"
    assert h["detail"].startswith("買いの判定4回のうち勝ち3回・負け1回。10営業日後の平均騰落率+0.5%。対象期間：X")


def test_headline_without_small_sample_flag_at_ten():
    h = tv.headline("sell", {"buy": [], "sell": [_res("sell", 10, 6)]}, "P")
    assert h["win_rate_pct"] == 60 and h["small_sample"] is False and h["small_sample_text"] is None


def test_headline_with_zero_samples_and_neutral():
    h = tv.headline("sell", {"buy": [], "sell": [_res("sell", 0, 0)]}, "P")
    assert h["kind"] == "no_samples" and h["win_rate_pct"] is None and "勝率は出せません" in h["text"]
    n = tv.headline("neutral", {"buy": [], "sell": []}, "P")
    assert n["kind"] == "neutral" and n["win_rate_pct"] is None


def test_result_cells_show_small_sample_and_none():
    cell = tv._result_json(_res("buy", 3, 2))
    assert cell["small_sample"] and "サンプルが少なく参考値" in cell["note"]
    assert cell["win_rate_display"] == 67
    empty = tv._result_json(_res("buy", 0, 0))
    assert empty["win_rate_pct"] is None and empty["note"] == "該当なし"


# --------------------------------------------------------------------------- end to end on the synthetic stocks

@pytest.fixture
def api(make_settings, make_client, synthetic):
    settings = make_settings()
    client = make_client(settings)
    conn = connect(settings.db_path)
    store_synthetic(conn, synthetic)
    conn.close()
    return client


def test_ma_cross_backtest_matches_plain_python(api, synthetic):
    """90050 (cross_stock): crosses from a plain-Python SMA, non-overlap and win
    counting done by hand here, compared with the API."""
    closes = [r["AdjC"] for r in synthetic["cross_stock"]]
    crosses = expected_crosses(closes)
    body = api.get("/api/stocks/90050/technical-backtest?indicator=ma_cross").json()
    for side, kind in (("buy", "golden"), ("sell", "dead")):
        days = [e["t"] for e in crosses if e["kind"] == kind]
        assert body["backtest"]["raw_signal_days"][side] == len(days)
        for cell in body["backtest"]["results"][side]:
            h = cell["horizon"]
            kept, last = [], None
            for d in days:
                if last is None or d >= last + h:
                    kept.append(d)
                    last = d
            done = [d for d in kept if d + h < len(closes)]
            rets = [closes[d + h] / closes[d] - 1 for d in done]
            wins = sum(1 for r in rets if (r > 0 if side == "buy" else r < 0))
            assert cell["signals"] == len(kept)
            assert cell["samples"] == len(done)
            assert cell["wins"] == wins
            if done:
                assert cell["win_rate_pct"] == pytest.approx(wins / len(done) * 100, abs=0.01)
                assert cell["avg_return_pct"] == pytest.approx(sum(rets) / len(rets) * 100, abs=1e-3)
            assert cell["small_sample"] == (len(done) < 10)


def test_rsi_backtest_counts_match_independent_loop(api, make_settings, synthetic):
    from stockapp.technicals import analyze
    from stockapp.db import connect as c2

    body = api.get("/api/stocks/90010/technical-backtest?indicator=rsi").json()
    conn = c2(make_settings().db_path)
    try:
        frame = analyze(conn, "90010").frame
    finally:
        conn.close()
    closes = frame["close"].tolist()
    rsi = frame["rsi"].tolist()
    for side in ("buy", "sell"):
        days = [i for i, v in enumerate(rsi) if not math.isnan(v)
                and (round(v, 1) <= 30 if side == "buy" else round(v, 1) >= 70)]
        cell = next(c for c in body["backtest"]["results"][side] if c["horizon"] == 10)
        kept = []
        for d in days:
            if not kept or d >= kept[-1] + 10:
                kept.append(d)
        done = [d for d in kept if d + 10 < len(closes)]
        wins = sum(1 for d in done if (closes[d + 10] > closes[d]) == (side == "buy") and closes[d + 10] != closes[d])
        assert (cell["signals"], cell["samples"], cell["wins"]) == (len(kept), len(done), wins)


def test_api_response_shape(api):
    r = api.get("/api/stocks/9001/technical-backtest?indicator=rsi")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "90010" and body["state"] == "ok" and body["indicator"] == "rsi"
    assert [i["key"] for i in body["indicators"]] == ["rsi", "macd", "ma_cross", "bollinger"]
    assert body["labels"]["title"] == "テクニカルで見る"
    ev = body["evaluation"]
    assert set(ev) >= {"side", "text", "basis", "values", "as_of"}
    assert ev["side"] in ("buy", "sell", "neutral")
    bt = body["backtest"]
    assert bt["horizons"] == [5, 10, 20] and bt["main_horizon"] == 10 and bt["min_samples"] == 10
    assert bt["costs_included"] is False
    assert bt["period"]["from"] == "2023-01-04" and bt["period"]["bars"] == 420
    assert bt["period"]["text"] in bt["table_caption"]
    for side in ("buy", "sell"):
        assert [c["horizon"] for c in bt["results"][side]] == [5, 10, 20]
        for c in bt["results"][side]:
            assert set(c) >= {"signals", "samples", "pending", "wins", "losses", "draws", "win_rate_pct",
                              "avg_return_pct", "small_sample", "note", "entries"}
            assert c["wins"] + c["losses"] + c["draws"] == c["samples"] == len(c["entries"])
    joined = " ".join(bt["conditions"])
    for must in ("終値（調整後）で入り", "5・10・20営業日後", "空売りの損益ではなく", "1回として数え",
                 "10回未満", "手数料・スプレッド・税金は考慮していません"):
        assert must in joined, must
    assert body["notes"][0].startswith("過去の結果は将来を保証しません")
    h = body["headline"]
    assert h["kind"] in ("rate", "no_samples", "neutral")
    if h["kind"] == "rate":
        assert h["detail"] and bt["period"]["text"] in h["detail"]


@pytest.mark.parametrize("indicator", tv.INDICATORS)
def test_every_indicator_text_passes_the_exception_word_check(api, indicator):
    for code in ("90010", "90020", "90030", "90040", "90050", "90060", "12340"):
        body = api.get(f"/api/stocks/{code}/technical-backtest?indicator={indicator}").json()
        for s in strings(body):
            assert not find_banned(s, allow=TECH_VIEW_ALLOWED), (code, s)


def test_api_rejects_unknown_indicator_and_bad_code(api):
    assert api.get("/api/stocks/90010/technical-backtest?indicator=stochastic").status_code == 422
    assert api.get("/api/stocks/" + "9" * 13 + "/technical-backtest").status_code == 400
    default = api.get("/api/stocks/90010/technical-backtest").json()
    assert default["indicator"] == "rsi"


def test_api_without_bars_is_no_data(api):
    body = api.get("/api/stocks/7203/technical-backtest?indicator=macd").json()
    assert body["state"] == "no_data" and body["backtest"] is None
    assert body["evaluation"]["side"] == "unavailable"
    assert body["headline"]["kind"] == "unavailable"


def test_short_history_has_no_rate(api):
    body = api.get("/api/stocks/90060/technical-backtest?indicator=rsi").json()   # 10 bars
    assert body["evaluation"]["side"] == "unavailable"
    assert all(c["samples"] == 0 and c["note"] == "該当なし"
               for s in ("buy", "sell") for c in body["backtest"]["results"][s])
