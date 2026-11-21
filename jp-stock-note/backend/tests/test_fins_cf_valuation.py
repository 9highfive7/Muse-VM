"""要件 5.4 CF metrics and 5.5 valuation from market-cap fixtures, point-in-time
switching at disclosure dates, split continuity and the 5-year percentile
position (受け入れ条件③④⑥)."""
from __future__ import annotations

import math

import pytest

import fins_fixtures as fx
from fins_fixtures import OKU, stmt
from stockapp import financials, fundamentals as fm

CODE = "20010"


def val_rows(code=CODE, f_div_2q=30):
    """FY2024-03 (disclosed 2024-05-10): equity 1,800, next-year NP forecast 160.
    FY2025-03 (disclosed 2025-05-09, Fri): NP 150, equity 2,000, EPS 75, DPS 30,
    CF 180 / −60 / −50, next-year forecast NP 200 / EPS 100 / DPS 30.
    2Q FY2026-03 (disclosed 2025-11-05): equity 2,100, forecast NP 220, EPS 110."""
    f24s, f24e = fx.fy_dates(2024)
    f25s, f25e = fx.fy_dates(2025)
    f26s, f26e = fx.fy_dates(2026)
    return [
        stmt(code, "V1", "2024-05-10", "FY", f24s, f24e, sales=2000, op=200, np_=120, eq=1800, eqar=0.45,
             eps=60, div_ann=25, nx_start=f25s, nx_end=f25e, nx_sales=2200, nx_op=220, nx_np=160, nx_eps=80,
             nx_div=30),
        stmt(code, "V2", "2025-05-09", "FY", f25s, f25e, sales=2200, op=230, np_=150, eq=2000, eqar=0.5, eps=75,
             div_ann=30, cfo=180, cfi=-60, cff=-50, nx_start=f26s, nx_end=f26e, nx_sales=2400, nx_op=260,
             nx_np=200, nx_eps=100, nx_div=30),
        stmt(code, "V3", "2025-11-05", "2Q", f26s, f26e, sales=1150, op=130, np_=105, eq=2100, eqar=0.5,
             f_sales=2400, f_op=280, f_np=220, f_eps=110, f_div=f_div_2q),
    ]


def val_bars(code=CODE, split_on=None):
    """Close 1,500円, market cap 300,000 (百万円 = 3,000億円) every day. With
    ``split_on`` a 1:2 split: AdjFactor 0.5 that day, raw close 750 after."""
    out = []
    for d in fx.weekdays("2024-01-04", "2025-12-30"):
        if split_on and d >= split_on:
            out.append((d, 750.0, 0.5 if d == split_on else 1.0, 300000.0))
        else:
            out.append((d, 1500.0, 1.0, 300000.0))
    return out


@pytest.fixture
def val_conn(db_conn):
    fx.store(db_conn, val_rows())
    fx.store_bars(db_conn, CODE, val_bars())
    return db_conn


def series(conn, code=CODE):
    recs = fm.load_records(conn, code)
    s = fm.valuation_series(recs, fm.load_valuation_bars(conn, code))
    return s.set_index("date")


# --------------------------------------------------------------------------- CF

def test_cf_metrics_hand_computed(val_conn):
    cf = financials.financials_payload(val_conn, CODE)["cf"]
    assert cf["fy_end"] == "2025-03-31"
    assert cf["ocf_to_profit"] == pytest.approx(1.2)            # 180 ÷ 150
    assert cf["simple_fcf"] == 120 * OKU                        # 180 + (−60)
    assert cf["pattern"] == {"operating": "+", "investing": "-", "financing": "-"}
    assert cf["pattern_label"] == "営業CF＋・投資CF−・財務CF−"
    assert cf["guideposts"] == {"reference": 1.0, "check": 0.5}
    assert any("一般的なフリーCFの定義とは一致しない" in n for n in cf["notes"])   # 必須注記（受け入れ条件⑥）


def test_ocf_ratio_not_computed_when_profit_nonpositive(db_conn):
    fs, fe = fx.fy_dates(2025)
    fx.store(db_conn, [stmt(CODE, "L1", "2025-05-09", "FY", fs, fe, sales=100, op=-5, np_=-10, cfo=20, cfi=-5,
                            cff=0)])
    cf = financials.financials_payload(db_conn, CODE)["cf"]
    assert cf["ocf_to_profit"] is None and cf["ocf_to_profit_reason"] == "純利益が0以下の場合は算出しない"
    assert cf["simple_fcf"] == 15 * OKU
    assert cf["pattern"]["financing"] == "0"


# --------------------------------------------------------------------------- valuation

def test_current_valuation_hand_computed(val_conn):
    v = financials.valuation_payload(val_conn, CODE)
    assert v["as_of"] == "2025-12-30"
    assert v["market_cap"]["value_yen"] == 3000 * OKU            # 300,000 百万円
    assert v["per"]["value"] == pytest.approx(3000 / 220, abs=1e-2)      # 時価総額 ÷ 予想純利益
    assert v["per"]["inputs"]["forecast_profit"] == 220 * OKU
    assert v["pbr"]["value"] == pytest.approx(3000 / 2100, abs=1e-2)     # 時価総額 ÷ 自己資本
    assert v["pbr"]["estimated"] is True
    assert v["estimated_shares"]["value"] == 200_000_000                 # 3,000億円 ÷ 1,500円
    assert v["pbr"]["inputs"]["estimated_bps"] == 1050.0                 # 2,100億円 ÷ 2億株
    assert 1500 / 1050 == pytest.approx(v["pbr"]["value"], abs=1e-2)     # 終値 ÷ 推定BPS と同値
    assert v["roe"]["actual"]["value"] == pytest.approx(150 / 1900 * 100, abs=1e-2)   # 150 ÷ ((1800+2000)/2)
    assert v["roe"]["forecast"]["value"] == pytest.approx(220 / 2100 * 100, abs=1e-2)
    assert v["roe"]["forecast"]["estimated"] is True
    assert v["dividend_yield"]["value"] == pytest.approx(2.0)            # 30 ÷ 1,500 × 100
    assert v["payout"]["actual"]["value"] == pytest.approx(40.0)         # 30 ÷ 75 × 100
    assert v["payout"]["forecast"]["value"] == pytest.approx(30 / 110 * 100, abs=1e-2)
    assert any("推定値" in n for n in v["notes"])                       # PBR推定の注記（受け入れ条件⑥）


def test_values_switch_exactly_at_disclosure_dates(val_conn):
    s = series(val_conn)
    # before the FY2025-03 results: forecast NP 160 (from the FY2024-03 statement), equity 1,800
    assert s.loc["2025-05-08", "per"] == pytest.approx(3000 / 160)
    assert s.loc["2025-05-08", "pbr"] == pytest.approx(3000 / 1800)
    # disclosure day 2025-05-09: next-year forecast NP 200, equity 2,000
    assert s.loc["2025-05-09", "per"] == pytest.approx(15.0)
    assert s.loc["2025-05-09", "pbr"] == pytest.approx(1.5)
    assert s.loc["2025-11-04", "per"] == pytest.approx(15.0)
    assert s.loc["2025-11-05", "per"] == pytest.approx(3000 / 220)
    assert s.loc["2025-11-05", "pbr"] == pytest.approx(3000 / 2100)
    # before any disclosure: not computable (NaN, never 0)
    assert math.isnan(s.loc["2024-05-09", "per"]) and math.isnan(s.loc["2024-05-09", "pbr"])
    assert s.loc["2024-05-10", "per"] == pytest.approx(3000 / 160)


def test_split_does_not_break_per_pbr_or_yield(db_conn):
    fx.store(db_conn, val_rows(f_div_2q=15))            # 2Q forecast already on the post-split basis
    fx.store_bars(db_conn, CODE, val_bars(split_on="2025-07-01"))
    s = series(db_conn)
    for col in ("per", "pbr", "dividend_yield"):
        assert s.loc["2025-06-30", col] == pytest.approx(s.loc["2025-07-01", col]), col
    assert s.loc["2025-07-01", "dividend_yield"] == pytest.approx(2.0)   # 30 × 0.5 ÷ 750 × 100
    assert s.loc["2025-07-01", "adj_mult"] == pytest.approx(0.5)
    assert s.loc["2025-11-05", "dividend_yield"] == pytest.approx(2.0)   # 15 × 1 ÷ 750 × 100 (new d0)


def test_no_market_cap_means_absent_not_zero(db_conn):
    fx.store(db_conn, val_rows())
    fx.store_bars(db_conn, CODE, [(d, c, f, None) for d, c, f, _ in val_bars()])
    v = financials.valuation_payload(db_conn, CODE)
    assert v["per"]["value"] is None and v["per"]["reason"] == "時価総額が未取得"
    assert v["pbr"]["value"] is None and v["market_cap"]["value_yen"] is None
    assert v["dividend_yield"]["value"] == pytest.approx(2.0)            # does not need the market cap


def test_loss_forecast_makes_per_not_computable(db_conn):
    rows = val_rows()
    rows[2]["FNP"] = str(-5 * OKU)
    fx.store(db_conn, rows)
    fx.store_bars(db_conn, CODE, val_bars())
    v = financials.valuation_payload(db_conn, CODE)
    assert v["per"]["value"] is None and v["per"]["reason"] == "予想が赤字またはゼロ"
    card = financials.business_cards(db_conn, CODE)["cards"][2]
    assert card["sentences"][0]["text"].startswith("予想PERは算出不可（予想が赤字またはゼロ）")


# --------------------------------------------------------------------------- percentile (5y)

def _pct_linear(xs, q):
    s = sorted(xs)
    h = (len(s) - 1) * q / 100
    lo = math.floor(h)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (h - lo)


def test_percentile_band_on_synthetic_five_years(db_conn):
    code = "20020"
    fs, fe = fx.fy_dates(2019)
    ns, ne = fx.fy_dates(2020)
    fx.store(db_conn, [stmt(code, "P1", "2019-05-10", "FY", fs, fe, sales=100, op=10, np_=8, eq=1000,
                            nx_start=ns, nx_end=ne, nx_np=100)])          # forecast NP 100億円
    days = fx.weekdays("2020-01-01", "2026-11-20")
    as_of = days[-1]
    window_start = "2021-11-20"                                           # as_of − 5 years (exclusive)
    pers = {}
    i = 0
    for d in days:
        if d <= window_start:
            pers[d] = 1000.0                                              # outside the window: must be ignored
        else:
            pers[d] = float((i * 37) % 101 + 5)
            i += 1
    pers[as_of] = 30.0
    # PER = 時価総額 ÷ 100億円 → market cap (百万円) = PER × 10,000
    fx.store_bars(db_conn, code, [(d, 1000.0, 1.0, pers[d] * 10_000) for d in days])
    window = [pers[d] for d in days if d > window_start]
    v = financials.valuation_payload(db_conn, code)
    p = v["per"]["percentile"]
    assert p["state"] == "ok" and p["n"] == len(window) and p["years"] == 5
    assert p["from"] == next(d for d in days if d > window_start) and p["to"] == as_of
    assert p["current"] == 30.0
    expected = sum(1 for x in window if x <= 30.0) / len(window) * 100
    assert p["percentile"] == pytest.approx(expected, abs=1e-3)
    for q in (25, 50, 75):
        assert p[f"p{q}"] == pytest.approx(_pct_linear(window, q), abs=1e-3)
    assert p["p25"] <= p["p50"] <= p["p75"]
    card = financials.business_cards(db_conn, code)["cards"][2]
    text = card["sentences"][0]["text"]
    assert f"下から{round(expected)}%の位置" in text and f"{len(window):,}営業日" in text
    assert "2021-11〜2026-11" in text


def test_percentile_needs_250_observations(db_conn):
    code = "20030"
    fs, fe = fx.fy_dates(2025)
    ns, ne = fx.fy_dates(2026)
    fx.store(db_conn, [stmt(code, "Q1", "2025-05-09", "FY", fs, fe, sales=100, op=10, np_=8, eq=1000,
                            nx_start=ns, nx_end=ne, nx_np=100)])
    # 2025-05-09〜2026-03-31 holds only 233 weekdays, so the range must extend
    # past the fiscal year end to yield 249 bars. PER stays computable there:
    # the FY2026 forecast remains 今期予想 until an FY2026 actual is disclosed.
    days = fx.weekdays("2025-05-09", "2026-06-30")[:250]
    assert len(days) == 250
    fx.store_bars(db_conn, code, [(d, 1000.0, 1.0, 150_000.0) for d in days[:249]])
    v = financials.valuation_payload(db_conn, code)
    assert v["per"]["value"] == pytest.approx(15.0)
    assert v["per"]["percentile"]["state"] == "insufficient" and v["per"]["percentile"]["n"] == 249
    assert v["per"]["percentile"]["percentile"] is None
    card = financials.business_cards(db_conn, code)["cards"][2]
    assert "観測不足" in card["sentences"][0]["text"]
    # boundary: the 250th computable observation makes the percentile available
    fx.store_bars(db_conn, code, [(days[249], 1000.0, 1.0, 150_000.0)])
    p = financials.valuation_payload(db_conn, code)["per"]["percentile"]
    assert p["state"] == "ok" and p["n"] == 250 and p["percentile"] == pytest.approx(100.0)
