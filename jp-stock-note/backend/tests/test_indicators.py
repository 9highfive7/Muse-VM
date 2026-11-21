"""受け入れ条件②: SMA・RSI・MACD・ボリンジャーバンド・ベータについて、3銘柄×任意3日の
値が手計算と小数第2位まで一致する（テストとして固定）。

The "hand calculation" is an independent plain-Python implementation of the
formulas in 要件 5.1・5.2 (lists and loops only, no pandas/numpy), evaluated
on the fixed synthetic series in fixtures/synthetic_bars.json. A few tiny
series also have literal expected values.
"""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import pytest

from stockapp import indicators as ind
from stockapp import technicals

CODES = ("90010", "90020", "90030")      # 3 stocks
DAY_INDEXES = (260, 333, 419)            # 3 business days (each >= 251 bars for beta)


# --------------------------------------------------------------------------- plain-Python reference

def p_sma(c: list[float], n: int, t: int) -> float | None:
    if t < n - 1:
        return None
    return sum(c[t - n + 1:t + 1]) / n


def p_rsi(c: list[float], t: int, n: int = 14) -> float | None:
    if t < n:
        return None
    ups = [max(c[i] - c[i - 1], 0.0) for i in range(1, t + 1)]      # ups[k] = U(k+1)
    downs = [max(c[i - 1] - c[i], 0.0) for i in range(1, t + 1)]
    au = sum(ups[:n]) / n
    ad = sum(downs[:n]) / n
    for k in range(n, t):
        au = (au * (n - 1) + ups[k]) / n
        ad = (ad * (n - 1) + downs[k]) / n
    if ad == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + au / ad)


def p_ema(x: list[float | None], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(x)
    start = next(i for i, v in enumerate(x) if v is not None)
    seed = start + n - 1
    value = sum(x[start:seed + 1]) / n
    out[seed] = value
    alpha = 2.0 / (n + 1)
    for t in range(seed + 1, len(x)):
        value = alpha * x[t] + (1 - alpha) * value
        out[t] = value
    return out


def p_macd(c: list[float], t: int) -> tuple[float, float, float]:
    e12, e26 = p_ema(c, 12), p_ema(c, 26)
    line = [None if a is None or b is None else a - b for a, b in zip(e12, e26)]
    sig = p_ema(line, 9)
    return line[t], sig[t], line[t] - sig[t]


def p_bollinger(c: list[float], t: int, n: int = 20, k: float = 2.0) -> tuple[float, float, float, float]:
    w = c[t - n + 1:t + 1]
    mid = sum(w) / n
    sd = math.sqrt(sum((v - mid) ** 2 for v in w) / n)      # population σ (divide by n)
    upper, lower = mid + k * sd, mid - k * sd
    return mid, upper, lower, (c[t] - lower) / (upper - lower)


def p_beta(c: list[float], tpx: list[float], vol: list[float], t: int) -> tuple[float, float]:
    """Last 250 daily returns ending at t; returns on zero-volume days excluded."""
    pairs = []
    for i in range(max(1, t - 249), t + 1):
        if vol[i] == 0:
            continue
        pairs.append((c[i] / c[i - 1] - 1.0, tpx[i] / tpx[i - 1] - 1.0))
    assert len(pairs) >= 200
    n = len(pairs)
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    cov = sum((x - mx) * (y - my) for x, y in pairs) / n
    vx = sum((x - mx) ** 2 for x, _ in pairs) / n
    vy = sum((y - my) ** 2 for _, y in pairs) / n
    return cov / vy, cov / math.sqrt(vx * vy)


# --------------------------------------------------------------------------- helpers

def to_2dp(x: float) -> Decimal:
    """Round half-up to 2 decimals the way a spreadsheet would, after removing
    binary float noise (846.555 stored as 846.55499999… must still be 846.56)."""
    return Decimal(repr(round(float(x), 9))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def same_2dp(actual, expected) -> bool:
    """Equal to the 2nd decimal (and numerically identical well beyond it)."""
    return actual is not None and expected is not None and to_2dp(actual) == to_2dp(expected) \
        and abs(actual - expected) < 1e-6


def series(synthetic, code):
    rows = synthetic["stocks"][code]
    return [r["AdjC"] for r in rows], [r["AdjVo"] for r in rows], [r["Date"] for r in rows]


@pytest.fixture(scope="module")
def frames(synthetic, tmp_path_factory):
    from conftest import store_synthetic
    from stockapp.db import connect, migrate

    conn = connect(tmp_path_factory.mktemp("ind") / "ind.db")
    migrate(conn)
    store_synthetic(conn, synthetic)
    out = {code: technicals.analyze(conn, code) for code in CODES}
    conn.close()
    return out


# --------------------------------------------------------------------------- fixed literal cases

def test_literal_small_series():
    c = pd.Series([float(i) for i in range(1, 31)])           # 1..30
    assert ind.sma(c, 5).iloc[-1] == 28.0
    assert math.isnan(ind.sma(c, 5).iloc[3])                 # fewer than n values -> missing, not partial
    assert ind.rsi_wilder(c)["rsi"].iloc[-1] == 100.0        # AvgD = 0 -> 100
    bb = ind.bollinger(c).iloc[-1]                            # window 11..30
    assert round(bb["bb_mid"], 2) == 20.50
    assert round(bb["bb_sd"], 4) == 5.7663                  # population σ = sqrt((20²−1)/12)
    assert round(bb["bb_upper"], 2) == 32.03 and round(bb["bb_lower"], 2) == 8.97
    assert round(bb["pct_b"], 2) == 0.91
    # sample σ (ddof=1) would be 5.9161 — make sure that is NOT what we use
    assert round(bb["bb_sd"], 4) != 5.9161


def test_literal_rsi_mixed_moves():
    # diffs: +1,-1 alternating (14 diffs) -> AvgU = 0.5, AvgD = 0.5 -> RSI 50
    c = pd.Series([10.0, 11.0] * 7 + [10.0])
    r = ind.rsi_wilder(c)
    assert math.isnan(r["rsi"].iloc[13])
    assert r["rsi"].iloc[14] == 50.0
    # one more +2 day: AvgU = (0.5×13+2)/14, AvgD = 0.5×13/14
    c2 = pd.Series(list(c) + [12.0])
    au, ad = (0.5 * 13 + 2) / 14, (0.5 * 13) / 14
    assert round(ind.rsi_wilder(c2)["rsi"].iloc[-1], 2) == round(100 - 100 / (1 + au / ad), 2) == 56.67


def test_literal_ema_seed():
    c = pd.Series([float(v) for v in range(1, 13)])          # 1..12
    e = ind.ema(c, 12)
    assert math.isnan(e.iloc[10]) and e.iloc[11] == 6.5      # seeded with the SMA of the first 12
    e3 = ind.ema(pd.Series([1.0, 2.0, 3.0, 4.0]), 3)
    assert e3.iloc[2] == 2.0 and e3.iloc[3] == 0.5 * 4.0 + 0.5 * 2.0


# --------------------------------------------------------------------------- 3 stocks × 3 days

@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("t", DAY_INDEXES)
def test_sma_matches_hand_calculation(frames, synthetic, code, t):
    c, _, dates = series(synthetic, code)
    row = frames[code].frame.iloc[t]
    assert row["date"] == dates[t]
    for n in (5, 25, 75, 200):
        assert same_2dp(row[f"sma{n}"], p_sma(c, n, t)), (code, dates[t], n)
        expected_dev = (c[t] - p_sma(c, n, t)) / p_sma(c, n, t) * 100
        assert same_2dp(row[f"dev{n}"], expected_dev)


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("t", DAY_INDEXES)
def test_rsi_matches_hand_calculation(frames, synthetic, code, t):
    c, _, dates = series(synthetic, code)
    assert same_2dp(frames[code].frame.iloc[t]["rsi"], p_rsi(c, t)), (code, dates[t])


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("t", DAY_INDEXES)
def test_macd_matches_hand_calculation(frames, synthetic, code, t):
    c, _, dates = series(synthetic, code)
    line, sig, hist = p_macd(c, t)
    row = frames[code].frame.iloc[t]
    assert same_2dp(row["macd"], line), (code, dates[t])
    assert same_2dp(row["macd_signal"], sig)
    assert same_2dp(row["macd_hist"], hist)


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("t", DAY_INDEXES)
def test_bollinger_matches_hand_calculation(frames, synthetic, code, t):
    c, _, dates = series(synthetic, code)
    mid, upper, lower, pct_b = p_bollinger(c, t)
    row = frames[code].frame.iloc[t]
    assert same_2dp(row["bb_mid"], mid), (code, dates[t])
    assert same_2dp(row["bb_upper"], upper)
    assert same_2dp(row["bb_lower"], lower)
    assert same_2dp(row["pct_b"], pct_b)


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("t", DAY_INDEXES)
def test_beta_matches_hand_calculation(frames, synthetic, code, t):
    c, vol, dates = series(synthetic, code)
    tpx = [r["C"] for r in synthetic["topix"]]
    assert [r["Date"] for r in synthetic["topix"]] == dates
    beta, corr = p_beta(c, tpx, vol, t)
    a = frames[code]
    res = ind.beta_correlation(a.aligned[a.aligned["date"] <= dates[t]])
    assert same_2dp(res.beta, beta), (code, dates[t])
    assert same_2dp(res.correlation, corr)
    assert res.window_to == dates[t]


def test_zero_volume_days_excluded_from_beta(frames, synthetic):
    """90030 has zero-volume days at 150, 151, 300: the latest 250-return window
    (indexes 170..419) contains one of them, so 249 returns are used."""
    a = frames["90030"]
    assert ind.beta_correlation(a.aligned).observations == 249
    assert ind.beta_correlation(frames["90010"].aligned).observations == 250


@pytest.mark.parametrize("code", CODES)
def test_api_values_match_hand_calculation(synthetic_conn, synthetic, code):
    """The same numbers through the /technicals payload (latest day)."""
    c, vol, _ = series(synthetic, code)
    t = len(c) - 1
    p = technicals.technicals_payload(synthetic_conn, code, "1y")
    v = p["values"]
    assert same_2dp(v["sma25"], p_sma(c, 25, t))
    assert same_2dp(v["sma200"], p_sma(c, 200, t))
    assert same_2dp(v["rsi"], p_rsi(c, t))
    line, sig, hist = p_macd(c, t)
    assert same_2dp(v["macd"], line) and same_2dp(v["macd_signal"], sig) and same_2dp(v["macd_hist"], hist)
    mid, upper, lower, pct_b = p_bollinger(c, t)
    assert same_2dp(v["bb_upper"], upper) and same_2dp(v["bb_lower"], lower) and same_2dp(v["pct_b"], pct_b)
    beta, corr = p_beta(c, [r["C"] for r in synthetic["topix"]], vol, t)
    assert to_2dp(p["stage4"]["beta"]["beta"]) == to_2dp(beta)
    assert to_2dp(p["stage4"]["beta"]["correlation"]) == to_2dp(corr)
    # the RSI series in the chart panel ends with the same value
    assert p["series"]["rsi"][-1] == round(p_rsi(c, t), 4)


def test_52_week_high_low(frames, synthetic):
    rows = synthetic["stocks"]["90010"]
    t = len(rows) - 1
    from datetime import date, timedelta

    start = (date.fromisoformat(rows[t]["Date"]) - timedelta(days=364)).isoformat()
    window = [r for r in rows if start <= r["Date"] <= rows[t]["Date"]]
    h52, l52 = max(r["AdjH"] for r in window), min(r["AdjL"] for r in window)
    row = frames["90010"].frame.iloc[t]
    assert same_2dp(row["h52"], h52) and same_2dp(row["l52"], l52)
    c = rows[t]["AdjC"]
    assert same_2dp(row["from_high_pct"], (c / h52 - 1) * 100)
    assert same_2dp(row["range_pos"], (c - l52) / (h52 - l52) * 100)
