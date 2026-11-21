"""Phase 2 API shapes, on-demand fetch endpoint, banned words (要件 6.1) on
the new templates / glossary / generated sentences, and missing-data
behaviour (bank-like company without operating profit: nulls, not zeros)."""
from __future__ import annotations

import json
from datetime import date

import pytest

import fins_fixtures as fx
from conftest import FakeTransport, make_jq_client
from fins_fixtures import stmt
from stockapp import financials, technicals, wording
from stockapp.db import connect
from stockapp.jquants.client import ENDPOINT_FINS_SUMMARY
from stockapp.jquants.transport import HttpResponse
from stockapp.wording import BANNED_WORDS, find_banned
from test_banned_words import strings
from test_fins_alerts import _progress_rows
from test_fins_cf_valuation import val_bars, val_rows
from test_fins_revisions import typed_company

# terms added with the Phase 2 frontend financial section
FIN_TERMS_UI = ("dividend_yield", "operating_margin", "quarterly_standalone", "yoy", "valuation_percentile",
                "cash_flow", "forecast_type")
FIN_TERMS = ("per", "pbr", "roe", "progress_rate", "operating_cf", "fcf", "payout_ratio", "equity_ratio",
             "forecast_revision") + FIN_TERMS_UI
BANK = "83010"


def _bank_quarter_disc(fy_end_year: int, k: int) -> str:
    """Disclosure date of quarter k (1..3) of the March fiscal year ending in
    fy_end_year: 1Q end-July, 2Q end-October (previous calendar year), 3Q
    end-January of fy_end_year. (Phase 2 built "{y-1}-13-28" for 3Q — an
    invalid date that only sorted correctly by string accident; same order,
    now a real date.)"""
    month = 4 + 3 * k                       # 7, 10, 13 -> 13 rolls into January
    year = fy_end_year - 1 + (month - 1) // 12
    return date(year, (month - 1) % 12 + 1, 28).isoformat()


def bank_rows(code=BANK):
    """Bank-like: Sales (経常収益) and NP disclosed, operating profit / its
    forecast never disclosed (""), no CF in quarterly statements."""
    rows = []
    for y in (2022, 2023, 2024, 2025):
        fs, fe = fx.fy_dates(y)
        ns, ne = fx.fy_dates(y + 1)
        for k, pt in enumerate(("1Q", "2Q", "3Q"), 1):
            rows.append(stmt(code, f"K{y}{k}", _bank_quarter_disc(y, k), pt, fs, fe, sales=250 * k,
                             np_=40 * k, eq=3000, eqar=0.05, f_sales=1000, f_np=160, f_eps=50, f_div=20))
        rows.append(stmt(code, f"K{y}4", f"{y}-05-12", "FY", fs, fe, sales=1000, np_=160, eq=3000, eqar=0.05,
                         eps=50, div_ann=20, cfo=-300, cfi=100, cff=50, nx_start=ns, nx_end=ne, nx_sales=1000,
                         nx_np=160, nx_eps=50, nx_div=20))
    fs, fe = fx.fy_dates(2026)
    rows.append(stmt(code, "K20261", "2025-07-28", "1Q", fs, fe, sales=260, np_=45, eq=3100, eqar=0.049,
                     f_sales=1000, f_np=170, f_eps=52, f_div=20))
    return rows


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, obj


def _is_count_field(path: str) -> bool:
    """Count / metadata leaves (number of valid years, required years, sample
    sizes) — legitimately 0, and not monetary or ratio values."""
    leaf = path.rsplit(".", 1)[-1].split("[", 1)[0]
    return leaf == "n" or leaf.startswith("n_") or "_n_" in leaf or leaf.endswith("_n") \
        or leaf.endswith("_required")


@pytest.fixture
def api(make_settings, make_client):
    settings = make_settings()
    client = make_client(settings)
    conn = connect(settings.db_path)
    fx.store(conn, fx.march_company("10010"))
    fx.store_bars(conn, "10010", [(d, 1500.0, 1.0, 300000.0) for d in fx.weekdays("2021-11-01", "2026-11-20")])
    fx.store(conn, bank_rows())
    fx.store_bars(conn, BANK, [(d, 800.0, 1.0, 120000.0) for d in fx.weekdays("2024-01-04", "2026-11-20")])
    conn.close()
    client.db_path = settings.db_path
    return client


# --------------------------------------------------------------------------- API

def test_financials_endpoint_shape(api):
    r = api.get("/api/stocks/1001/financials")           # 4-digit code accepted
    assert r.status_code == 200
    b = r.json()
    assert b["code"] == "10010" and b["state"] == "ok" and b["as_of"] == "2026-11-05"
    assert len(b["annual"]["rows"]) == 5 and b["annual"]["forecast"]["fy_end"] == "2027-03-31"
    row = b["annual"]["rows"][-1]
    assert {"net_sales", "operating_profit", "profit", "operating_margin", "disclosed_date"} <= set(row)
    assert row["operating_margin"] == pytest.approx(10.0)
    assert len(b["quarterly"]["rows"]) == 8
    p = b["progress"]
    assert {"actual_pct", "pattern_avg_pct", "deviation_pt"} <= set(p["items"]["operating_profit"])
    assert isinstance(b["revisions"]["years"], list) and b["revisions"]["type"]["type"] == "stable"
    assert b["cf"]["pattern_label"] and b["cf"]["ocf_to_profit"] is not None
    assert "items" in b["alerts"] and b["alerts"]["as_of"] == "2026-11-05"
    assert "/v2/fins/summary" in b["source_note"]


def test_valuation_endpoint_shape(api):
    b = api.get("/api/stocks/10010/valuation").json()
    assert b["state"] == "ok" and b["as_of"] == "2026-11-20"
    for k in ("per", "pbr", "dividend_yield"):
        assert {"value", "available", "reason", "as_of", "percentile"} <= set(b[k])
        assert b[k]["as_of"] == "2026-11-20"
    assert b["pbr"]["estimated"] is True and b["estimated_shares"]["estimated"] is True
    assert {"actual", "forecast"} <= set(b["roe"]) and {"actual", "forecast"} <= set(b["payout"])
    pc = b["per"]["percentile"]
    assert {"p25", "p50", "p75", "percentile", "n", "from", "to"} <= set(pc)
    assert len(b["series"]["dates"]) == len(b["series"]["per"]) == len(b["series"]["pbr"])


def test_business_cards_endpoint(api):
    b = api.get("/api/stocks/10010/cards/business").json()
    assert [c["title"] for c in b["cards"]] == ["業績方向", "財務健全性", "バリュエーション・マクロ位置"]
    for c in b["cards"]:
        assert 1 <= len(c["sentences"]) <= 4
        for s in c["sentences"]:
            # Phase 3: the valuation card's macro line scrolls to the macro section
            assert "基準日：" in s["text"] and s["as_of"] and s["target"].startswith(("fin-", "macro-"))
    val = b["cards"][2]
    # Phase 3 filled the macro part (was "phase3_pending"). No FRED data in this
    # fixture -> one explicit "not fetched" line, not an empty list.
    assert val["macro_state"] == "no_data"
    assert [m["key"] for m in val["parts"]["macro"]] == ["macro_rates"]
    assert val["parts"]["macro"][0]["available"] is False


def test_glossary_financial_terms_with_values(api):
    for term in FIN_TERMS:
        r = api.get(f"/api/glossary/{term}?code=10010")
        assert r.status_code == 200, term
        b = r.json()
        assert b["definition"] and b["misconception"] and "{" not in b["formula"], term
        if term != "forecast_revision":         # this company never revised its forecast
            assert b["formula_is_generic"] is False, term
    pbr = api.get("/api/glossary/pbr?code=10010").json()
    assert pbr["formula"] == "PBR ＝ 時価総額 3,000億円 ÷ 自己資本 2,600億円 ＝ 1.15倍（推定値。基準日：2026-11-20）"
    generic = api.get("/api/glossary/per").json()
    assert generic["formula_is_generic"] is True and generic["code"] is None


def test_glossary_ui_terms_substitute_this_stocks_values(api):
    g = {t: api.get(f"/api/glossary/{t}?code=10010").json() for t in FIN_TERMS_UI}
    assert g["operating_margin"]["formula"] == "営業利益率 ＝ 営業利益 140億円 ÷ 売上高 1,400億円 × 100 ＝ 10.0%（2026年3月期）"
    assert g["yoy"]["formula"] == ("前年同期比 ＝ (第2四半期累計の売上高 750億円 − 前年同期 700億円) ÷ |前年同期 700億円|"
                                   " × 100 ＝ +7.1%（基準日：2026-11-05）")
    assert g["quarterly_standalone"]["formula"] == ("2027年3月期 Q2単独の売上高 ＝ 第2四半期累計 750億円 − 前四半期までの累計"
                                                    " 375億円 ＝ 375億円")
    assert g["dividend_yield"]["formula"].endswith("＝ 2.00%（基準日：2026-11-20）")
    assert g["cash_flow"]["formula"] == "営業CF 168億円、投資CF −70.0億円、財務CF −42.0億円（2026年3月期の開示値）"
    assert "完了済み5期のうち" in g["forecast_type"]["formula"]
    assert "観測日数" in g["valuation_percentile"]["formula"] and "{" not in g["valuation_percentile"]["formula"]
    # bank-like: no operating profit -> generic margin formula, never a fabricated value
    m = api.get(f"/api/glossary/operating_margin?code={BANK}").json()
    assert m["formula_is_generic"] is True and m["formula_note"]


def test_unknown_stock_returns_no_data(api):
    b = api.get("/api/stocks/99990/financials").json()
    assert b["state"] == "no_data" and b["alerts"]["none_text"] == "決算サマリーが未取得のため判定していない"
    v = api.get("/api/stocks/99990/valuation").json()
    assert v["state"] == "no_data" and v["per"]["value"] is None and v["per"]["reason"] == "日足が未取得"
    c = api.get("/api/stocks/99990/cards/business").json()
    assert c["cards"][0]["sentences"][0]["key"] == "no_fins"


def test_ensure_fins_endpoint_fetches_once(make_settings, make_client):
    rows = fx.march_company("13010")
    transport = FakeTransport(handler=lambda req: HttpResponse(200, {"data": rows}))
    jq = make_jq_client(transport)
    client = make_client(make_settings(), jq_client=jq)
    first = client.post("/api/stocks/1301/ensure-fins").json()
    assert first["state"] == "fetched" and first["has_fins"] is True
    assert [r.params for r in transport.requests if r.url.endswith(ENDPOINT_FINS_SUMMARY)] == [{"code": "13010"}]
    second = client.post("/api/stocks/1301/ensure-fins").json()
    assert second["state"] == "stored" and len(transport.requests) == 1
    forced = client.post("/api/stocks/1301/ensure-fins?force=true").json()
    assert forced["state"] == "fetched" and len(transport.requests) == 2
    assert client.get("/api/stocks/1301/financials").json()["state"] == "ok"
    status = client.get("/api/status").json()
    assert status["counts"]["fins_summary"] == len(rows) and status["corrections"] == []


def test_ensure_fins_without_key_reports_reason(make_settings, make_client):
    client = make_client(make_settings())
    out = client.post("/api/stocks/1301/ensure-fins").json()
    assert out["state"] == "failed" and "APIキーが未設定" in out["error_reason"] and out["has_fins"] is False


# --------------------------------------------------------------------------- missing data (bank-like)

def test_bank_like_company_returns_nulls_not_zeros(api):
    b = api.get(f"/api/stocks/{BANK}/financials").json()
    assert b["state"] == "ok"
    for row in b["annual"]["rows"]:
        assert row["operating_profit"] is None and row["operating_margin"] is None
        assert row["net_sales"] is not None and row["profit"] is not None
    for q in b["quarterly"]["rows"]:
        assert q["values"]["operating_profit"] is None
        assert q["reasons"]["operating_profit"] == "未開示"
    op = b["progress"]["items"]["operating_profit"]
    assert op["actual_pct"] is None and op["actual_reason"] == "未開示"
    assert b["progress"]["primary_item"] == "profit"
    assert b["progress"]["items"]["profit"]["actual_pct"] == pytest.approx(45 / 170 * 100, abs=1e-2)
    assert b["yoy"]["items"]["operating_profit"]["kind"] == "not_disclosed"
    assert b["revisions"]["type"]["type"] == "insufficient"           # landing rates are on operating profit
    # operating CF negative vs positive profit: a fact, computed; FCF computed
    assert b["cf"]["ocf_to_profit"] == pytest.approx(-300 / 160, abs=1e-4)
    # The historical progress pattern has no valid years (operating profit never
    # disclosed): its *count* metadata is a genuine 0, while every value is null.
    assert op["pattern_n_valid"] == 0 and op["pattern_required"] == 3 and op["pattern_state"] != "ok"
    assert op["pattern_avg_pct"] is None and op["pattern_min_pct"] is None and op["pattern_max_pct"] is None
    assert op["deviation_pt"] is None and op["beyond_threshold"] is False
    assert all(y["pct"] is None for y in op["pattern_years"])
    # nothing anywhere in the payload is a fabricated 0 for an operating-profit field
    # (count / metadata leaves such as pattern_n_valid are not financial values)
    scanned = 0
    for path, value in walk(b):
        if "operating_profit" in path and "reason" not in path and "label" not in path \
                and not _is_count_field(path):
            assert value is None or isinstance(value, (str, bool)) or value != 0, path
            scanned += 1
    assert scanned > 20
    cards = api.get(f"/api/stocks/{BANK}/cards/business").json()["cards"]
    perf = [s["text"] for s in cards[0]["sentences"]]
    assert any("営業利益は未開示" in t for t in perf)
    assert any("通期純利益予想" in t for t in perf)
    v = api.get(f"/api/stocks/{BANK}/valuation").json()
    assert v["per"]["value"] == pytest.approx(1200 / 170, abs=1e-2)   # market cap 1,200億円 ÷ NP forecast
    assert all(api.get(f"/api/glossary/{t}?code={BANK}").status_code == 200 for t in FIN_TERMS)
    prog = api.get(f"/api/glossary/progress_rate?code={BANK}").json()
    assert prog["formula_is_generic"] is True and prog["formula_note"]


def test_bank_like_alert_for_low_operating_cf_ratio(api):
    rules = {a["rule"] for a in api.get(f"/api/stocks/{BANK}/financials").json()["alerts"]["items"]}
    assert "ocf_to_profit_low" in rules        # −1.88 < 0.5 (profit > 0)


# --------------------------------------------------------------------------- banned words (6.1)

def test_new_banned_examples_are_listed():
    for w in ("好決算", "悪い決算", "サプライズ", "業績好調", "財務は安全", "高配当でお得", "減配の心配", "割安", "割高"):
        assert w in BANNED_WORDS
        assert find_banned(f"文{w}文")


def test_phase2_templates_and_glossary_are_clean():
    raw = json.loads(wording.TEMPLATES_PATH.read_text(encoding="utf-8"))
    fin = {k: v for k, v in raw.items() if k.startswith("fin.")}
    assert len(fin) > 100
    for k, v in fin.items():
        assert not find_banned(v), (k, find_banned(v))
    fg = json.loads(wording.FIN_GLOSSARY_PATH.read_text(encoding="utf-8"))
    assert set(FIN_TERMS) <= set(fg)
    for text in strings(fg):
        assert not find_banned(text), text
    for term, entry in wording.fin_glossary().items():
        for key in ("label", "definition", "formula", "formula_generic", "misconception"):
            assert entry.get(key), (term, key)


def test_generated_phase2_sentences_are_clean(db_conn):
    fixtures = {
        "10010": fx.march_company("10010"),
        "10030": [dict(r, Code="10030") for r in _progress_rows(60)],
        "10050": typed_company([(100, [110], 112), (100, [90], 88), (100, [], 102)]),
        "20010": val_rows(),
        BANK: bank_rows(),
    }
    for code, rows in fixtures.items():
        fx.store(db_conn, [dict(r, Code=code, DiscNo=f"{code}-{r['DiscNo']}") for r in rows])
    fx.store_bars(db_conn, "20010", val_bars())
    fx.store_bars(db_conn, "10010", [(d, 1500.0, 1.0, 300000.0) for d in fx.weekdays("2021-11-01", "2026-11-20")])
    n = 0
    for code in list(fixtures) + ["99990"]:
        texts = list(strings(financials.financials_payload(db_conn, code)))
        texts += list(strings(financials.valuation_payload(db_conn, code)))
        texts += list(strings(financials.business_cards(db_conn, code)))
        for term in FIN_TERMS:
            texts += list(strings(technicals.glossary_entry(db_conn, term, code)))
        for t in texts:
            assert not find_banned(t), (code, t, find_banned(t))
        n += len(texts)
    assert n > 500
