"""Cross history (要件 3.3 クロス発生履歴, 原則1の全件併記) and the chart /
technicals / card / glossary API shapes."""
from __future__ import annotations

import pytest

from conftest import store_synthetic
from stockapp.db import connect

CROSS_CODE = "90050"
SHORT_CODE = "90060"   # only 10 bars


@pytest.fixture
def api(make_settings, make_client, synthetic):
    settings = make_settings()
    client = make_client(settings)          # create_app migrates the DB
    conn = connect(settings.db_path)
    store_synthetic(conn, synthetic)
    conn.close()
    client.db_path = settings.db_path
    return client


def p_sma(c, n, t):
    return None if t < n - 1 else sum(c[t - n + 1:t + 1]) / n


def expected_crosses(closes: list[float]) -> list[dict]:
    """Plain-Python 5.1 definition: GC S(t-1)<=L(t-1) & S(t)>L(t); DC mirrored."""
    out = []
    for t in range(1, len(closes)):
        s0, l0, s1, l1 = p_sma(closes, 25, t - 1), p_sma(closes, 75, t - 1), p_sma(closes, 25, t), p_sma(closes, 75, t)
        if None in (s0, l0, s1, l1):
            continue
        kind = "golden" if s0 <= l0 and s1 > l1 else "dead" if s0 >= l0 and s1 < l1 else None
        if kind:
            ev = {"t": t, "kind": kind}
            for n in (20, 60):
                ev[n] = (closes[t + n] / closes[t] - 1) * 100 if t + n < len(closes) else None
            out.append(ev)
    return out


def walk_keys(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{path}.{k}", k
            yield from walk_keys(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_keys(v, f"{path}[{i}]")


# --------------------------------------------------------------------------- cross history

@pytest.mark.parametrize("rng", ["3m", "5y"])
def test_cross_history_lists_every_cross_with_forward_returns(api, synthetic, rng):
    rows = synthetic["cross_stock"]
    closes = [r["AdjC"] for r in rows]
    dates = [r["Date"] for r in rows]
    expected = expected_crosses(closes)
    assert len(expected) >= 4 and {e["kind"] for e in expected} == {"golden", "dead"}

    body = api.get(f"/api/stocks/{CROSS_CODE}/technicals?range={rng}").json()
    ch = body["cross_history"]
    events = ch["events"]
    # every cross in the stored history, regardless of the display range
    assert [(e["date"], e["kind"]) for e in events] == [(dates[x["t"]], x["kind"]) for x in expected]
    for e, x in zip(events, expected):
        for n in (20, 60):
            got = e[f"after_{n}d"]
            if x[n] is None:
                assert got == {"date": None, "return_pct": None}
            else:
                assert got["date"] == dates[x["t"] + n]
                assert round(got["return_pct"], 2) == round(x[n], 2)
        assert e["business_days_ago"] == len(closes) - 1 - x["t"]
        assert e["label"] in ("25日線が75日線を上抜け", "25日線が75日線を下抜け")

    # up AND down counts side by side, no averages / win rates
    for n in (20, 60):
        vals = [x[n] for x in expected]
        c = ch["counts"][f"after_{n}d"]
        assert c["up"] == sum(1 for v in vals if v is not None and v > 0)
        assert c["down"] == sum(1 for v in vals if v is not None and v < 0)
        assert c["pending"] == sum(1 for v in vals if v is None)
        assert c["total"] == len(expected)
        assert c["up"] > 0 and c["down"] > 0
    assert any("上昇" in t and "下落" in t for t in ch["count_texts"])
    assert "件数が少なく統計的な結論は出せない。" in ch["notes"]

    forbidden = ("win", "rate", "ratio", "avg", "average", "mean", "hit", "success", "probab", "勝率", "的中")
    for path, key in walk_keys(ch):
        assert not any(f in key.lower() for f in forbidden), path
    assert "勝率" not in str(ch) and "平均" not in str(ch)


def test_latest_cross_fact_in_stage1(api, synthetic):
    body = api.get(f"/api/stocks/{CROSS_CODE}/technicals").json()
    last = body["cross_history"]["events"][-1]
    s1 = body["stage1"]
    assert s1["latest_cross"]["date"] == last["date"]
    fact = next(f for f in s1["facts"] if f["key"] == "latest_cross")
    assert last["date"] in fact["text"] and f"{last['business_days_ago']}営業日前" in fact["text"]


# --------------------------------------------------------------------------- chart shape

def test_chart_payload_shape_with_markers(api):
    with connect(api.db_path) as conn:
        conn.executemany(
            "INSERT INTO fins_summary(disclosure_no, code, disclosed_date, doc_type, period_type, raw_json,"
            " content_sha256, fetched_at) VALUES (?,?,?,?,?,'{}','x','x')",
            [("D1", "90020", "2024-05-10", "FYFinancialStatements_Consolidated_JP", "FY"),   # Friday
             ("D2", "90020", "2024-08-03", "1QFinancialStatements_Consolidated_JP", "1Q"),   # Saturday
             ("D3", "90020", "2020-01-10", "FYFinancialStatements_Consolidated_JP", "FY"),   # before history
             ("D4", "90010", "2024-05-10", "FY", "FY")],                                    # other code
        )
    body = api.get("/api/stocks/9002/chart?range=5y")   # 4-digit code accepted
    assert body.status_code == 200
    ch = body.json()
    assert ch["code"] == "90020" and ch["state"] == "ok" and ch["range"] == "5y"
    n = len(ch["dates"])
    assert n == len(ch["candles"]) == 420
    for key in ("sma5", "sma25", "sma75", "sma200"):
        assert len(ch["ma"][key]) == n
    assert ch["ma"]["sma200"][198] is None and ch["ma"]["sma200"][199] is not None
    assert [m["line"] for m in ch["ma_styles"]] == ["thin_solid", "thick_solid", "dashed", "dotted"]
    c0 = ch["candles"][0]
    assert set(c0) >= {"date", "open", "high", "low", "close", "volume", "upper_limit", "lower_limit"}

    earnings = ch["markers"]["earnings"]
    assert [(m["date"], m["bar_date"]) for m in earnings] == [("2024-05-10", "2024-05-10"),
                                                               ("2024-08-03", "2024-08-05")]
    assert all(m["label"] == "決" for m in earnings)
    assert earnings[0]["disclosures"][0]["disclosure_no"] == "D1"

    # stop high/low flags (synthetic 90020: UL on day 210, LL on day 330)
    assert ch["markers"]["stop_high"] == [ch["dates"][210]]
    assert ch["markers"]["stop_low"] == [ch["dates"][330]]
    assert ch["candles"][210]["upper_limit"] is True and ch["candles"][330]["lower_limit"] is True
    assert sum(c["upper_limit"] for c in ch["candles"]) == 1
    assert any("分割" in t for t in ch["notes"]) and any("ストップ高・安" in t for t in ch["notes"])


@pytest.mark.parametrize("rng,months", [("3m", 3), ("6m", 6), ("1y", 12), ("3y", 36), ("5y", 60)])
def test_chart_ranges(api, rng, months):
    ch = api.get(f"/api/stocks/90010/chart?range={rng}").json()
    assert ch["range"] == rng and ch["range_end"] == "2024-08-13"
    import pandas as pd

    start = (pd.Timestamp("2024-08-13") - pd.DateOffset(months=months)).date().isoformat()
    assert ch["range_start"] > start
    full = api.get("/api/stocks/90010/chart?range=5y").json()
    offset = full["dates"].index(ch["range_start"])
    assert ch["dates"] == full["dates"][offset:]
    # MAs inside a range are computed from the full history, not restarted at the range start
    assert ch["ma"]["sma200"] == full["ma"]["sma200"][offset:]
    if rng in ("3m", "6m"):
        assert ch["ma"]["sma200"][0] is not None


def test_invalid_range_rejected(api):
    assert api.get("/api/stocks/90010/chart?range=2w").status_code == 422


def test_short_history_returns_nulls_not_errors(api):
    tech = api.get(f"/api/stocks/{SHORT_CODE}/technicals?range=1y")
    assert tech.status_code == 200
    t = tech.json()
    assert t["state"] == "ok" and len(t["dates"]) == 10
    v = t["values"]
    for key in ("rsi", "macd", "macd_signal", "macd_hist", "sma25", "sma75", "sma200", "bb_upper", "h52"):
        assert v[key] is None, key
    assert v["sma5"] is not None
    assert t["series"]["rsi"] == [None] * 10 and t["series"]["macd"] == [None] * 10
    beta = t["stage4"]["beta"]
    assert beta["beta"] is None and beta["correlation"] is None and beta["observations"] == 9
    unavailable = {f["key"] for s in ("stage1", "stage2", "stage3", "stage4") for f in t[s]["facts"]
                   if not f["available"]}
    assert {"ma_order", "rsi", "bollinger", "macd", "w52", "beta"} <= unavailable
    assert all("算出不可" in f["text"] or "判定不可" in f["text"]
               for s in ("stage1", "stage2", "stage3", "stage4") for f in t[s]["facts"] if not f["available"])
    assert t["cross_history"]["events"] == []

    ch = api.get(f"/api/stocks/{SHORT_CODE}/chart").json()
    assert ch["state"] == "ok" and ch["ma"]["sma25"] == [None] * 10

    card = api.get(f"/api/stocks/{SHORT_CODE}/cards/price-position")
    assert card.status_code == 200
    sentences = card.json()["sentences"]
    assert len(sentences) == 4 and not any(s["available"] for s in sentences)


def test_unknown_or_unfetched_stock_is_no_data(api):
    for path in ("/api/stocks/12340/chart", "/api/stocks/12340/technicals", "/api/stocks/12340/cards/price-position"):
        r = api.get(path)
        assert r.status_code == 200 and r.json()["state"] == "no_data", path


def test_price_position_card(api):
    card = api.get("/api/stocks/90010/cards/price-position").json()
    assert card["state"] == "ok" and card["title"] == "価格位置"
    s = card["sentences"]
    assert [x["key"] for x in s] == ["deviation", "ma_order", "w52", "excess60"]
    assert [x["target"] for x in s] == ["stage1", "stage1", "stage3", "stage4"]
    for x in s:
        assert x["available"] and x["as_of"] == "2024-08-13"
        assert f"（基準日：{x['as_of']}）" in x["text"]   # 各文に基準日を付ける
    assert s[0]["text"].startswith("終値が25日移動平均線を")
    assert "52週レンジの上から" in s[2]["text"]
    assert "過去60営業日の騰落率は銘柄" in s[3]["text"]


def test_glossary_substitutes_stock_values(api):
    body = api.get("/api/glossary/rsi?code=90010").json()
    tech = api.get("/api/stocks/90010/technicals").json()
    assert body["formula_is_generic"] is False and body["as_of"] == "2024-08-13"
    assert f"{tech['values']['rsi']:.1f}" in body["formula"]
    assert body["definition"] and body["misconception"]

    generic = api.get(f"/api/glossary/beta?code={SHORT_CODE}").json()
    assert generic["formula_is_generic"] is True and generic["formula_note"]
    assert "{" not in generic["formula"]

    no_code = api.get("/api/glossary/macd").json()
    assert no_code["formula_is_generic"] is True and no_code["code"] is None

    assert api.get("/api/glossary/unknown-term").status_code == 404


def test_glossary_all_terms_render_for_full_stock(api):
    from stockapp.wording import glossary

    for term in glossary():
        r = api.get(f"/api/glossary/{term}?code=90050")
        assert r.status_code == 200, term
        body = r.json()
        assert "{" not in body["formula"] and "}" not in body["formula"], term
        assert body["formula_is_generic"] is False, term
