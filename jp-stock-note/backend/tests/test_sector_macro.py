"""Phase 3 sector sensitivity master (all 17 sectors, 3 caution sectors),
the tailwind/headwind checklist (both sides, no score), /api/stocks/{code}/macro,
the valuation card's macro sentences, macro glossary terms, banned words
(要件 6.1) on every new template / content file / generated payload, and the
two Phase 2 leftovers (bank fixture date, TOPIX-less relative panel)."""
from __future__ import annotations

import json
from datetime import date

import pytest

import fins_fixtures as fx
from conftest import seed
from fred_fixtures import daily_dates, linear, months, seed_fred, seed_topix, standard_bodies
from stockapp import financials, fred, macro, technicals, wording
from stockapp.db import connect, migrate
from stockapp.ingest import store_stock_master
from stockapp.wording import find_banned
from test_banned_words import strings
from test_fins_api_and_words import bank_rows, walk

TOPIX17 = {
    "1": "食品", "2": "エネルギー資源", "3": "建設・資材", "4": "素材・化学", "5": "医薬品",
    "6": "自動車・輸送機", "7": "鉄鋼・非鉄", "8": "機械", "9": "電機・精密", "10": "情報通信・サービスその他",
    "11": "電力・ガス", "12": "運輸・物流", "13": "商社・卸売", "14": "小売", "15": "銀行",
    "16": "金融（除く銀行）", "17": "不動産",
}
EFFECTS = {"tailwind", "headwind", "neutral"}
MACRO_TERMS = ("long_term_rate", "yield_spread", "assumed_fx_rate", "cyclical_defensive", "tailwind_headwind")
BANK, REIT = "83060", "88010"


def extra_master():
    base = {"Date": "2026-11-20", "ScaleCat": "TOPIX Mid400", "Mkt": "0111", "MktNm": "プライム", "Mrgn": "2",
            "MrgnNm": "貸借", "ProdCat": "011"}
    return [
        {**base, "Code": BANK, "CoName": "テスト銀行", "CoNameEn": "TEST BANK", "S17": "15", "S17Nm": "銀行",
         "S33": "7050", "S33Nm": "銀行業"},
        {**base, "Code": REIT, "CoName": "テスト不動産", "CoNameEn": "TEST REAL ESTATE", "S17": "17",
         "S17Nm": "不動産", "S33": "8050", "S33Nm": "不動産業"},
    ]


def rising_rates() -> dict:
    """Rates rising over 60 observations (US daily, JP monthly)."""
    b = standard_bodies()
    us = daily_dates("2026-11-19", 300)
    b["DGS10"] = linear(us, 3.0, 0.005)               # +0.30 pt / 60 obs
    b["IRLTLT01JPM156N"] = linear(months("2005-11-01", 252), 0.2, 0.02)   # +0.06 / 3 months
    return b


@pytest.fixture
def world(db_conn):
    seed(db_conn)
    with db_conn:
        store_stock_master(db_conn, extra_master(), "2026-11-21T00:00:00+00:00")
    seed_fred(db_conn, rising_rates())
    seed_topix(db_conn)
    return db_conn


# --------------------------------------------------------------------------- master

def test_all_17_sectors_mapped_with_valid_tags():
    s = macro.sectors()
    assert set(s) == set(TOPIX17)
    for code, entry in s.items():
        assert entry["name"] == TOPIX17[code]
        assert entry["cycle"] in {"cyclical", "defensive", "mixed"}
        for cond in ("rate_up", "rate_down", "yen_weak", "yen_strong"):
            assert entry[cond] in EFFECTS, (code, cond)
        assert {"cycle", "rate", "fx"} <= set(entry["why"])
        # 金利上昇・低下（円安・円高）のタグは互いに逆向きか、ともに中立
        for a, b in (("rate_up", "rate_down"), ("yen_weak", "yen_strong")):
            pair = {entry[a], entry[b]}
            assert pair in ({"neutral"}, {"tailwind", "headwind"}), (code, a, b)


def test_three_heterogeneous_sectors_carry_caution():
    flagged = {k for k, v in macro.sectors().items() if v.get("caution")}
    assert flagged == {"10", "12", "16"}
    assert {macro.sectors()[k]["name"] for k in flagged} == {"情報通信・サービスその他", "運輸・物流", "金融（除く銀行）"}
    for k in flagged:
        assert macro.sectors()[k]["caution_reason"]
        p = macro.sector_profile(k, None)
        assert p["caution"]["flag"] is True and p["caution"]["label"] == "業種内の異質性が大きい区分"
    assert macro.sector_profile("15", None)["caution"]["flag"] is False


def test_profile_notes_general_tendency_and_33_sector():
    p = macro.sector_profile("6", "自動車・輸送機")
    assert "業種の一般的傾向であり、この銘柄の実測値ではない" in p["notes"][0]
    assert "33業種" in p["notes"][1] and "PER" in p["notes"][1] and "自己資本比率" in p["notes"][1]
    assert p["cycle"]["label"] == "景気敏感"
    labels = [t["label"] for t in p["tags"]]
    assert "円安：プラス方向" in labels and "円高：マイナス方向" in labels
    assert macro.sector_profile(None, "食品")["sector17"]["code"] == "1"          # name fallback
    assert macro.sector_profile("99", "その他")["state"] == "unmapped"


# --------------------------------------------------------------------------- checklist

def test_checklist_rate_sensitive_sector_rates_rising(world):
    out = macro.stock_macro_payload(world, BANK)
    assert out["profile"]["sector17"] == {"code": "15", "name": "銀行"}
    rows = {r["factor"]: r for r in out["checklist"]["rows"]}
    assert list(rows) == ["rates", "fx", "cycle"]
    rates = rows["rates"]
    assert rates["tags"] == {"rate_up": "tailwind", "rate_down": "headwind"}
    assert rates["tailwind_conditions"] == ["金利が上昇する局面"]
    assert rates["headwind_conditions"] == ["金利が低下する局面"]
    assert rates["relevant"] is True
    d = rates["current"]["direction"]
    assert d["us10y"] == {"20": "up", "60": "up"}
    assert d["jp10y"]["60"] == "up"
    cur = " ".join(rates["current"]["facts"])
    assert "60営業日で+0.30ポイント（上昇）" in cur and "日本10年金利" in cur
    fxrow = rows["fx"]
    assert fxrow["relevant"] is False and fxrow["tags"] == {"yen_weak": "neutral", "yen_strong": "neutral"}
    assert fxrow["tailwind_conditions"][0].startswith("該当なし") and fxrow["headwind_conditions"][0].startswith("該当なし")
    assert "景気拡大" in rows["cycle"]["tailwind_conditions"][0]
    # no score / verdict anywhere in the payload
    for path, _ in walk(out):
        leaf = path.rsplit(".", 1)[-1].lower()
        assert not any(w in leaf for w in ("score", "rating", "signal", "verdict", "current_side")), path


def test_checklist_opposite_sector_swaps_sides(world):
    rows = {r["factor"]: r for r in macro.stock_macro_payload(world, REIT)["checklist"]["rows"]}
    assert rows["rates"]["tailwind_conditions"] == ["金利が低下する局面"]
    assert rows["rates"]["headwind_conditions"] == ["金利が上昇する局面"]
    assert rows["rates"]["current"]["direction"]["us10y"]["60"] == "up"   # same facts, different tags


def test_checklist_fx_rows_show_direction_and_tankan(world):
    rows = {r["factor"]: r for r in macro.stock_macro_payload(world, "72030")["checklist"]["rows"]}
    f = rows["fx"]
    assert f["tags"] == {"yen_weak": "tailwind", "yen_strong": "headwind"}
    assert f["tailwind_conditions"] == ["円安（ドル円の上昇）の局面"]
    assert f["current"]["direction"]["usdjpy"] == {"20": "flat", "60": "flat"}   # +0.20 / +0.60 yen < 0.5%
    assert any("想定為替レート154.23円" in t for t in f["current"]["facts"])


def test_direction_flat_band():
    assert macro.direction(0.049, "rate") == "flat" and macro.direction(0.05, "rate") == "up"
    assert macro.direction(-0.3, "rate") == "down" and macro.direction(None, "rate") is None
    assert macro.direction(0.6, "fx") == "up" and macro.direction(-0.99, "topix") == "flat"


# --------------------------------------------------------------------------- endpoint

def test_stock_macro_endpoint(make_settings, make_client):
    settings = make_settings()
    conn = connect(settings.db_path)
    migrate(conn)
    seed(conn)
    seed_fred(conn)
    seed_topix(conn)
    conn.close()
    api = make_client(settings)
    b = api.get("/api/stocks/9433/macro").json()                     # KDDI: 情報通信・サービスその他
    assert b["state"] == "ok" and b["sector33"]["name"] == "情報・通信業"
    assert b["profile"]["caution"]["flag"] is True
    assert b["profile"]["cycle"]["value"] == "mixed"
    assert b["macro"]["reference_kind"] == "topix"          # KDDI has no stored bars in this fixture
    t = api.get("/api/stocks/7203/macro").json()
    assert t["macro"]["reference_kind"] == "stock" and t["macro"]["reference_date"] == "2026-11-20"
    assert t["profile"]["caution"]["flag"] is False
    assert {"per", "equity_ratio", "facts", "note"} <= set(b["context"])
    assert b["checklist"]["notes"] and "両方" in b["checklist"]["notes"][0]
    assert api.get("/api/stocks/99990/macro").status_code == 404
    assert api.get("/api/stocks/1234567890123/macro").status_code == 400


# --------------------------------------------------------------------------- valuation card

def test_valuation_card_macro_sentences(db_conn):
    fx.store(db_conn, fx.march_company("10010"))
    fx.store_bars(db_conn, "10010", [(d, 1500.0, 1.0, 300000.0) for d in fx.weekdays("2021-11-01", "2026-11-20")])
    seed_fred(db_conn)
    card = financials.business_cards(db_conn, "10010")["cards"][2]
    assert card["macro_state"] == "ok"
    keys = [s["key"] for s in card["sentences"]]
    assert keys == ["per", "pbr", "dividend", "macro_rates"] and len(card["sentences"]) <= 4
    m = card["sentences"][3]
    assert m["target"] == "macro-section" and "基準日：" in m["text"]
    assert "予想PER" in m["text"] and "日本10年金利" in m["text"] and "2026年10月の月次値" in m["text"]
    per = financials.valuation_payload(db_conn, "10010")["per"]["value"]
    assert wording.num(per, 2) in m["text"]
    assert [s["key"] for s in card["parts"]["macro"]] == ["macro_rates", "macro_fx"]
    assert "想定為替レート" in card["parts"]["macro"][1]["text"]


def test_valuation_card_chips_for_mapped_stock(world):
    m = macro.card_sentences(world, BANK, None, None)
    assert m["chips"] == ["景気敏感", "金利上昇：プラス方向", "円安：小さい"]
    assert m["sentences"][0]["available"] is True and "予想PER" not in m["sentences"][0]["text"]


# --------------------------------------------------------------------------- glossary

def test_macro_glossary_three_part_and_values(world):
    g = wording.macro_glossary()
    assert set(g) == set(MACRO_TERMS)
    for term, entry in g.items():
        for key in ("label", "definition", "formula", "formula_generic", "misconception"):
            assert entry.get(key), (term, key)
    sp = technicals.glossary_entry(world, "yield_spread")               # market-wide: no code needed
    assert sp["formula_is_generic"] is False and "ポイント（基準日：2026-11-19）" in sp["formula"]
    fxg = technicals.glossary_entry(world, "assumed_fx_rate")
    assert "154.23円" in fxg["formula"] and "+5.70円" in fxg["formula"]
    cd = technicals.glossary_entry(world, "cyclical_defensive")
    assert cd["formula_is_generic"] is True                              # sector needs a code
    cd = technicals.glossary_entry(world, "cyclical_defensive", BANK)
    assert "「銀行」の区分：景気敏感" in cd["formula"]


def test_glossary_endpoint_macro_terms(make_client):
    api = make_client()
    for term in MACRO_TERMS:
        r = api.get(f"/api/glossary/{term}")
        assert r.status_code == 200, term
        assert r.json()["formula_is_generic"] is True and r.json()["formula_note"]


# --------------------------------------------------------------------------- banned words

def test_new_content_files_are_clean():
    files = [wording.MACRO_GLOSSARY_PATH, macro.SENSITIVITY_PATH, fred.SERIES_PATH]
    for f in files:
        raw = json.loads(f.read_text(encoding="utf-8"))
        for t in strings(raw):
            assert not find_banned(t), (f.name, t, find_banned(t))
    templates = json.loads(wording.TEMPLATES_PATH.read_text(encoding="utf-8"))
    macro_t = {k: v for k, v in templates.items() if k.startswith(("macro.", "s4.topix"))}
    assert len(macro_t) > 60
    for k, v in macro_t.items():
        assert not find_banned(v), (k, v)


def test_generated_macro_payloads_are_clean(world):
    fx.store(world, fx.march_company("10010"))
    texts = list(strings(macro.macro_payload(world)))
    for code in ("72030", "94330", "87250", "130A0", "20800", BANK, REIT, "10010"):
        texts += list(strings(macro.stock_macro_payload(world, code)))
        texts += list(strings(macro.card_sentences(world, code, 12.3, "2026-11-20")))
        for term in MACRO_TERMS:
            texts += list(strings(technicals.glossary_entry(world, term, code)))
    texts += list(strings(financials.business_cards(world, "10010")))
    assert len(texts) > 300
    for t in texts:
        assert not find_banned(t), (t, find_banned(t))


def test_generated_macro_payloads_clean_without_data(db_conn):
    seed(db_conn)
    texts = list(strings(macro.macro_payload(db_conn)))
    texts += list(strings(macro.stock_macro_payload(db_conn, "72030")))
    for t in texts:
        assert not find_banned(t), t


# --------------------------------------------------------------------------- Phase 2 leftovers

def test_bank_fixture_dates_are_valid():
    for r in bank_rows():
        date.fromisoformat(r["DiscDate"])                 # was "YYYY-13-28" for 3Q
    third = [r["DiscDate"] for r in bank_rows() if r["CurPerType"] == "3Q"]
    assert third == ["2022-01-28", "2023-01-28", "2024-01-28", "2025-01-28"]


def test_relative_panel_states_topix_missing(db_conn):
    fx.store_bars(db_conn, "10010", [(d, 1500.0, 1.0, 300000.0) for d in fx.weekdays("2025-01-01", "2026-11-20")])
    s4 = technicals.technicals_payload(db_conn, "10010", "1y")["stage4"]
    assert s4["topix_available"] is False and s4["topix_message"].startswith("TOPIXデータ未取得")
    seed_topix(db_conn)
    s4 = technicals.technicals_payload(db_conn, "10010", "1y")["stage4"]
    assert s4["topix_available"] is True and s4["topix_message"] is None
