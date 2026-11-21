"""Phase 3 macro calculations and /api/macro: cadence badges and as-of per
series (受け入れ条件①, research-03 lag asymmetry), 20/60/250 windows on every
headline series, 250-observation range position, US spread on common dates
only, the 短観 gap, stale-badge emphasis (受け入れ条件④), settings endpoint."""
from __future__ import annotations

from datetime import date

import pytest

from fred_fixtures import daily_dates, linear, months, seed_fred, seed_topix, standard_bodies
from stockapp import macro
from stockapp.app_settings import SettingError, get_tankan, set_tankan
from stockapp.db import connect


@pytest.fixture
def seeded(db_conn):
    seed_fred(db_conn)
    seed_topix(db_conn)
    return db_conn


# --------------------------------------------------------------------------- cadence / as-of

def test_every_item_has_as_of_and_cadence_badge(seeded):
    m = macro.macro_payload(seeded)
    assert m["headline"] == ["topix", "us10y", "us_spread", "usdjpy", "jp10y", "call_rate"]
    for key, item in m["items"].items():
        assert item["as_of"], key                       # 欠落を自動テストで検出（受け入れ条件①）
        assert item["cadence_badge"], key
        assert item["lag_note"] and item["source"], key
        for f in item["facts"]:
            assert f["as_of"], (key, f)


def test_badges_reflect_research_03_lag_findings(seeded):
    it = macro.macro_payload(seeded)["items"]
    assert it["us10y"]["cadence_badge"] == "日次（米国営業日）"
    assert it["us_spread"]["cadence_badge"] == "日次（米国営業日）"
    assert "週次更新のため最大約1週遅れ" in it["usdjpy"]["cadence_badge"]
    assert it["jp10y"]["cadence_badge"] == "月次（約1か月遅れ）"
    assert it["call_rate"]["cadence_badge"] == "月次（約1か月遅れ）"
    assert it["topix"]["cadence_badge"] == "日次（東証営業日）"
    assert "前営業日" in it["us10y"]["lag_note"]
    assert "半月〜1か月" in it["jp10y"]["lag_note"]


def test_monthly_jp10y_shows_observation_month(seeded):
    jp = macro.macro_payload(seeded)["items"]["jp10y"]
    assert jp["frequency"] == "monthly"
    assert jp["as_of"] == "2026-10-01" and jp["as_of_month"] == "2026-10"
    level = jp["facts"][0]["text"]
    assert "2026年10月の月次値" in level and "基準月：2026年10月" in level
    assert jp["history"]["step"] is True                      # ステップ線（受け入れ条件②）
    assert jp["month_change"]["change"] == pytest.approx(0.005)
    assert "IRSTCB01" in macro.macro_payload(seeded)["items"]["call_rate"]["note"]   # 代替である旨


def test_dexjpus_has_weekly_lag_badge_and_as_of(seeded):
    fx = macro.macro_payload(seeded)["items"]["usdjpy"]
    assert fx["as_of"] == "2026-11-13" and fx["series_id"] == "DEXJPUS"
    assert "最大約1週遅れ" in fx["cadence_badge"]


def test_stale_badge_emphasis(seeded):
    """Reference = TOPIX 2026-11-20. DEXJPUS (11-13) is 5 business days
    older -> emphasised; DGS10 (11-19) is not; monthly 2026-10 is the normal lag."""
    it = macro.macro_payload(seeded)["items"]
    assert it["usdjpy"]["stale"]["stale"] is True and it["usdjpy"]["stale"]["lag"] == 5
    assert "5営業日古い" in it["usdjpy"]["stale"]["text"]
    assert it["us10y"]["stale"]["stale"] is False
    assert it["jp10y"]["stale"]["stale"] is False and it["jp10y"]["stale"]["lag"] == 1


def test_monthly_stale_after_three_months(db_conn):
    b = standard_bodies()
    b["IRLTLT01JPM156N"] = b["IRLTLT01JPM156N"][:-3]            # last obs 2026-07-01
    seed_fred(db_conn, b)
    seed_topix(db_conn)
    jp = macro.macro_payload(db_conn)["items"]["jp10y"]
    assert jp["as_of"] == "2026-07-01" and jp["stale"]["stale"] is True and jp["stale"]["lag"] == 4


# --------------------------------------------------------------------------- windows

def test_headline_series_carry_20_60_250_changes(seeded):
    m = macro.macro_payload(seeded)
    for key in m["headline"]:
        ch = m["items"][key]["changes"]
        assert set(ch) == {"20", "60", "250"}, key           # never a 1-day change alone
        for n in ("20", "60", "250"):
            assert {"n", "available", "from_date", "change"} <= set(ch[n])
            assert ch[n]["available"] is True, (key, n)
        assert "1" not in ch
        assert m["items"][key]["range_250"]["state"] == "ok", key


def test_change_values(seeded):
    it = macro.macro_payload(seeded)["items"]
    us = it["us10y"]["changes"]
    assert us["20"]["change"] == pytest.approx(0.04) and us["60"]["change"] == pytest.approx(0.12)
    assert us["250"]["change"] == pytest.approx(0.5)
    assert us["60"]["from_date"] == daily_dates("2026-11-19", 300)[-61]
    tp = it["topix"]["changes"]["20"]
    last = 2500.0 + 299
    assert tp["change_pct"] == pytest.approx((last / (last - 20) - 1) * 100, abs=1e-6)
    assert it["usdjpy"]["changes"]["20"]["change"] == pytest.approx(0.2)
    assert it["usdjpy"]["changes"]["20"]["change_pct"] is not None
    sp = it["us_spread"]["changes"]["60"]["change"]
    assert sp == pytest.approx(0.06)                          # 0.002-0.001 per day × 60


def test_insufficient_history_marks_windows_unavailable(db_conn):
    b = standard_bodies()
    b["DGS10"] = b["DGS10"][-30:]
    seed_fred(db_conn, b)
    us = macro.macro_payload(db_conn)["items"]["us10y"]
    assert us["changes"]["20"]["available"] is True
    assert us["changes"]["60"]["available"] is False and us["changes"]["60"]["change"] is None
    assert us["range_250"] == {"state": "insufficient", "n": 30, "window": 250}
    assert "算出不可" in us["facts"][0]["text"]


def test_range_position_definition():
    pts = [(f"d{i:03d}", float(v)) for i, v in enumerate(list(range(250)))]
    rp = macro.range_position(pts)
    assert rp["percentile"] == pytest.approx(100.0) and rp["min"] == 0 and rp["max"] == 249
    pts[-1] = ("d249", 49.5)                                    # 50 values (0..49) ≤ 49.5, plus itself
    assert macro.range_position(pts)["percentile"] == pytest.approx(51 / 250 * 100)
    assert macro.range_position(pts[:249])["state"] == "insufficient"


# --------------------------------------------------------------------------- spread alignment

def test_spread_only_on_common_dates(db_conn):
    b = standard_bodies()
    d10 = dict(b["DGS10"])
    d2 = dict(b["DGS2"])
    dates = sorted(d10)
    # US holiday style gaps: DGS10 missing on one day, DGS2 missing ('.' -> None) on another,
    # and a date that exists only in DGS2
    gap10, gap2 = dates[-5], dates[-3]
    d10.pop(gap10)
    d2[gap2] = None
    d2["2026-11-20"] = 3.40
    b["DGS10"] = sorted(d10.items())
    b["DGS2"] = sorted(d2.items())
    seed_fred(db_conn, b)
    from stockapp import fred

    pts = macro.aligned_spread(fred.observations(db_conn, "DGS10"), fred.observations(db_conn, "DGS2"))
    pdates = [d for d, _ in pts]
    assert gap10 not in pdates and gap2 not in pdates and "2026-11-20" not in pdates
    assert len(pts) == len(dates) - 2
    sp = macro.macro_payload(db_conn)["items"]["us_spread"]
    assert sp["as_of"] == dates[-1] == "2026-11-19"
    assert sp["value"] == pytest.approx(d10[dates[-1]] - d2[dates[-1]])
    assert sp["legs"] == {"long": pytest.approx(d10[dates[-1]]), "short": pytest.approx(d2[dates[-1]])}
    assert sp["sign"] == "positive" and "プラス" in sp["facts"][0]["text"]


def test_spread_negative_sign_text(db_conn):
    us = daily_dates("2026-11-19", 300)
    seed_fred(db_conn, {**standard_bodies(), "DGS10": linear(us, 3.0, 0.0), "DGS2": linear(us, 3.5, 0.0)})
    sp = macro.macro_payload(db_conn)["items"]["us_spread"]
    assert sp["sign"] == "negative" and sp["value"] == pytest.approx(-0.5)
    assert "逆転" in sp["facts"][0]["text"]


# --------------------------------------------------------------------------- tankan gap

def test_tankan_gap_exact(seeded):
    fx = macro.macro_payload(seeded)["items"]["usdjpy"]
    assert fx["value"] == pytest.approx(159.93)
    tk = fx["tankan"]
    assert tk["rate"] == pytest.approx(154.23) and tk["survey_label"] == "日銀短観 2026年9月調査・全産業"
    assert tk["gap_yen"] == 5.70                               # 159.93 − 154.23
    assert tk["gap_pct"] == 3.70                               # 5.70 ÷ 154.23 × 100 = 3.6958…
    text = next(f["text"] for f in fx["facts"] if f["key"] == "tankan")
    assert text == ("ドル円159.93円は、日銀短観 2026年9月調査・全産業の想定為替レート154.23円を"
                    "5.70円（3.70%）上回る（基準日：2026-11-13）")
    assert "表示中銘柄の想定レートではない" in tk["note"]


def test_tankan_gap_function():
    g = macro.tankan_gap(150.0, 160.0)
    assert g["gap_yen"] == -10.0 and g["gap_pct"] == -6.25
    assert macro.tankan_gap(None, 154.23)["available"] is False


def test_default_setting_seeded_by_migration(db_conn):
    tk = get_tankan(db_conn)
    assert tk["value"] == 154.23 and tk["survey_label"] == "日銀短観 2026年9月調査・全産業"
    assert tk["origin"] == "default" and tk["updated_at"]


@pytest.mark.parametrize("value", [0, -1, "abc", None, True, float("nan"), float("inf"), 1000, "1e9"])
def test_setting_validation_rejects(db_conn, value):
    with pytest.raises(SettingError):
        set_tankan(db_conn, value, "日銀短観 2026年12月調査・全産業")
    assert get_tankan(db_conn)["value"] == 154.23


def test_settings_endpoint_validates_and_persists(make_settings, make_client):
    settings = make_settings()
    api = make_client(settings)
    assert api.get("/api/settings/tankan").json()["value"] == 154.23
    for bad in ({"value": -5, "survey_label": "x"}, {"value": "abc", "survey_label": "x"},
                {"value": 150}, {"value": 150, "survey_label": "  "}, {"value": 150, "survey_label": "あ" * 61}):
        r = api.post("/api/settings/tankan", json=bad)
        assert r.status_code == 422, bad
        assert isinstance(r.json()["detail"], str)
    r = api.post("/api/settings/tankan", content="value=150", headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 422                                # JSON only
    r = api.post("/api/settings/tankan", json={"value": "150.5", "survey_label": " 日銀短観 2026年12月調査・全産業 "})
    assert r.status_code == 200
    body = r.json()
    assert body["value"] == 150.5 and body["survey_label"] == "日銀短観 2026年12月調査・全産業"
    assert body["origin"] == "user" and body["updated_at"]
    conn = connect(settings.db_path)                            # persisted in the DB
    try:
        assert get_tankan(conn)["value"] == 150.5
        seed_fred(conn)
        fx = macro.macro_payload(conn)["items"]["usdjpy"]["tankan"]
        assert fx["gap_yen"] == 9.43 and fx["gap_pct"] == round(9.43 / 150.5 * 100, 2)
    finally:
        conn.close()
    assert api.get("/api/status").json()["tankan"]["value"] == 150.5


# --------------------------------------------------------------------------- API

def test_macro_endpoint_no_data_state(make_client):
    b = make_client().get("/api/macro").json()
    assert b["state"] == "no_data"
    for key in b["headline"]:
        it = b["items"][key]
        assert it["state"] == "no_data" and it["cadence_badge"] and set(it["changes"]) == {"20", "60", "250"}
        assert it["facts"][0]["available"] is False


def test_macro_endpoint_shape(make_settings, make_client):
    settings = make_settings()
    conn = connect(settings.db_path)
    from stockapp.db import migrate

    migrate(conn)
    seed_fred(conn)
    seed_topix(conn)
    conn.close()
    b = make_client(settings).get("/api/macro").json()
    assert b["state"] == "ok" and b["reference_date"] == "2026-11-20" and b["reference_kind"] == "topix"
    assert b["items"]["topix"]["ma"]["order"] == [25, 75, 200]
    assert len(b["items"]["jp10y"]["history"]["dates"]) == 120
    assert b["thresholds"]["rate_flat_pt"] == 0.05
    assert "FRED" in b["source_note"]
