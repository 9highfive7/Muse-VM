"""Phase 2 read-side payloads: /financials, /valuation, /cards/business, the
financial 確認ポイント (要件 3.7) and glossary substitution values.

DB only (never calls J-Quants). Calculations live in ``fundamentals``; every
sentence is rendered from content/templates.json via ``wording.render``.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Any

import pandas as pd

from . import fundamentals as fm
from .fins import revision_log
from .queries import resolve_code
from .wording import num, render, signed, yen

ENDPOINT_NOTE = ("データ出所：決算サマリー /v2/fins/summary（開示日・開示番号は各行に表示）、"
                 "時価総額・終値・調整係数 /v2/equities/bars/daily")


def _r(v: Any, digits: int = 4) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, digits)


def _reason(code: str | None) -> str | None:
    return None if code is None else render(f"fin.reason.{code}")


def _period(pt: str) -> str:
    return render(f"fin.period.{pt}")


def _period_end(pt: str) -> str:
    return render(f"fin.period_end.{pt}")


def _item(item: str) -> str:
    return render(f"fin.item.{item}")


def _sentence(key: str, template: str, as_of: str | None, available: bool = True, target: str | None = None,
              **values) -> dict:
    return {"key": key, "text": render(template, as_of=as_of or "—", **values), "as_of": as_of,
            "available": available, "target": target}


class Fins:
    """Everything derived from one code's stored summaries (computed once per request)."""

    def __init__(self, conn: sqlite3.Connection, code: str, upto: str | None = None):
        """``upto`` (YYYY-MM-DD) restricts to disclosures dated on/before it —
        used by the watchlist digest to compare 前営業日時点 vs now."""
        self.code = resolve_code(code)
        self.records = fm.load_records(conn, self.code)
        if upto is not None:
            self.records = [r for r in self.records if (r.get("disclosed_date") or "") <= upto]
        self.stmts = fm.statements(self.records)
        self.obs = fm.forecast_observations(self.records)
        self.empty = not self.records
        self.latest = fm.latest_statement(self.stmts)
        self.as_of = max((r["disclosed_date"] for r in self.records if r.get("disclosed_date")), default=None)
        self._cache: dict[str, Any] = {}

    def get(self, key: str):
        if key not in self._cache:
            fn = {
                "yoy": lambda: fm.latest_yoy(self.stmts),
                "progress": lambda: fm.progress(self.stmts, self.obs),
                "history": lambda: fm.revision_history(self.stmts, self.obs),
                "cf": lambda: fm.cf_metrics(self.stmts),
                "equity_ratio": lambda: fm.equity_ratio_change(self.stmts),
                "quarterly": lambda: fm.quarterly_standalone(self.stmts),
            }[key]
            self._cache[key] = fn()
        return self._cache[key]

    def current_revisions(self) -> tuple[dict | None, list[dict]]:
        """Revision history of the 今期 fiscal year (latest forecast target) and
        its sales / operating profit / net profit revision events."""
        hist = self.get("history")
        target = None
        for item in fm.ITEMS:
            f = fm.current_forecast(self.records, item)
            if f:
                target = f["fy_end"] if target is None else min(target, f["fy_end"])
        if target is None:
            return None, []
        year = next((h for h in hist if h["fy_end"] == target), None)
        if year is None:
            return None, []
        return year, [e for e in year["revisions"] if e["item"] in fm.ITEMS]


# --------------------------------------------------------------------------- phrases

def _yoy_phrase(v: dict) -> str:
    kind = v["kind"]
    if kind == "rate":
        return render("fin.yoy.rate", pct=signed(v["pct"]))
    if kind in ("turned_positive", "nonpositive_both"):
        return render(f"fin.yoy.{kind}", cur=yen(v["current"]), prior=yen(v["prior"]))
    return render("fin.yoy.not_disclosed")


def _change_phrase(e: dict) -> str:
    if e["rate"] is None:
        return render("fin.change.from_zero", item=_item(e["item"]), old=yen(e["old"]), new=yen(e["new"]))
    return render("fin.change.rate", item=_item(e["item"]), rate=signed(e["rate"]))


def _progress_item(p: dict) -> str | None:
    """Operating profit; net profit when operating profit is not disclosed (bank-like)."""
    if p.get("state") != "ok":
        return None
    op = p["items"]["operating_profit"]
    if fm._ok(op["actual"]["cumulative"]):
        return "operating_profit"
    if fm._ok(p["items"]["profit"]["actual"]["cumulative"]):
        return "profit"
    return "operating_profit"


# --------------------------------------------------------------------------- alerts (確認ポイント)

def alerts(f: Fins) -> dict:
    cfg = fm.rules()["alerts"]
    out: list[dict] = []
    if f.empty:
        return {"as_of": None, "items": [], "none_text": render("fin.alert.no_fins"), "thresholds": cfg,
                "notes": [render("fin.note.alerts")]}

    def add(rule: str, template: str, as_of: str | None, check: str, **values):
        out.append({"rule": rule, "text": render(template, as_of=as_of, **values), "as_of": as_of,
                    "check": render(check), "provisional": rule == "progress_deviation" and
                    bool(cfg["progress_deviation_provisional"])})

    # 1. 増収減益
    y = f.get("yoy")
    if y.get("state") == "ok":
        s, o = y["items"]["net_sales"], y["items"]["operating_profit"]
        if s["pct"] is not None and o["pct"] is not None and fm._r6(s["pct"]) > 0 and fm._r6(o["pct"]) < 0:
            add("revenue_up_profit_down", "fin.alert.rev_up_op_down", y["disclosed_date"],
                "fin.alert.rev_up_op_down.check", period=_period(y["period_type"]), sales=signed(s["pct"]),
                op=signed(o["pct"]), fiscal_year=fm.fy_label(y["fy_end"]))
    # 2. 営業CF÷純利益 < 0.5（純利益 > 0 の場合のみ）
    cf = f.get("cf")
    if cf.get("state") == "ok" and cf["ocf_to_profit"] is not None \
            and fm._r6(cf["ocf_to_profit"]) < cfg["ocf_to_profit_below"]:
        add("ocf_to_profit_low", "fin.alert.ocf_low", cf["disclosed_date"], "fin.alert.ocf_low.check",
            fiscal_year=cf["fiscal_year"], ratio=num(cf["ocf_to_profit"], 2))
    # 3. 進捗率の乖離 ±15pt 以上（暫定値）
    p = f.get("progress")
    item = _progress_item(p)
    if item:
        it = p["items"][item]
        dev = it["deviation_pt"]
        if dev is not None and abs(fm._r6(dev)) >= cfg["progress_deviation_pt"]:
            add("progress_deviation", "fin.alert.progress", p["disclosed_date"], "fin.alert.progress.check",
                period=_period(p["period_type"]), item=_item(item), pct=num(it["actual"]["pct"]),
                avg=num(it["pattern"]["average"]), dev=signed(dev), th=num(cfg["progress_deviation_pt"], 0))
    # 4. 配当性向 100% 超（実績または予想）
    pa, pf = fm.payout_actual(f.stmts), fm.payout_forecast(f.records)
    if pa.get("value") is not None and fm._r6(pa["value"]) > cfg["payout_ratio_above_pct"]:
        add("payout_over_100", "fin.alert.payout_actual", pa["disclosed_date"], "fin.alert.payout.check",
            pct=num(pa["value"]), fiscal_year=pa["fiscal_year"])
    if pf.get("value") is not None and fm._r6(pf["value"]) > cfg["payout_ratio_above_pct"]:
        add("payout_over_100", "fin.alert.payout_forecast", pf["disclosed_date"], "fin.alert.payout.check",
            pct=num(pf["value"]), fiscal_year=pf["fiscal_year"])
    # 5. 自己資本比率の低下（前年同期比 5pt 以上）
    e = f.get("equity_ratio")
    if e.get("state") == "ok" and e["diff_pt"] is not None and fm._r6(e["diff_pt"]) <= -cfg["equity_ratio_drop_pt"]:
        add("equity_ratio_drop", "fin.alert.equity_drop", e["disclosed_date"], "fin.alert.equity_drop.check",
            prior=num(e["prior_pct"]), cur=num(e["current_pct"]), diff=signed(e["diff_pt"]),
            period=_period_end(e["period_type"]), fiscal_year=fm.fy_label(e["fy_end"]))
    # 6. 予想の大幅修正（今期の通期予想、|修正率| >= 10%）
    year, events = f.current_revisions()
    for ev in events:
        if ev["rate"] is not None and abs(fm._r6(ev["rate"])) >= cfg["forecast_revision_abs_pct"]:
            add("forecast_revision", "fin.alert.revision", ev["disclosed_date"], "fin.alert.revision.check",
                date=ev["disclosed_date"], item=_item(ev["item"]), rate=signed(ev["rate"]),
                fiscal_year=fm.fy_label(ev["fy_end"]))
    return {"as_of": f.as_of, "items": out,
            "none_text": render("fin.alert.none", as_of=f.as_of) if not out else None,
            "thresholds": cfg, "notes": [render("fin.note.alerts")]}


# --------------------------------------------------------------------------- /financials

def _annual(f: Fins) -> dict:
    fys = [s for s in f.stmts if s["period_type"] == "FY"][-5:]
    rows = []
    prev_std = None
    all_fy = [s for s in f.stmts if s["period_type"] == "FY"]
    for s in fys:
        i = all_fy.index(s)
        prev_std = all_fy[i - 1].get("accounting_standard") if i > 0 else None
        sales, op, npf = s.get("net_sales"), s.get("operating_profit"), s.get("profit")
        margin = op / sales * 100.0 if fm._ok(op) and fm._ok(sales) and sales > 0 else None
        cfo, cfi = s.get("cf_operating"), s.get("cf_investing")
        rows.append({
            "kind": "actual", "fy_end": s["fy_end"], "fiscal_year": fm.fy_label(s["fy_end"]),
            "months": fm.months(s.get("fy_start"), s["fy_end"]),
            "irregular_year": not fm.is_regular(s.get("fy_start"), s["fy_end"]),
            "accounting_standard": s.get("accounting_standard"),
            "standard_changed": prev_std is not None and prev_std != s.get("accounting_standard"),
            "disclosed_date": s["disclosed_date"], "disclosure_no": s["disclosure_no"],
            "net_sales": sales, "operating_profit": op, "profit": npf, "operating_margin": _r(margin),
            "total_assets": s.get("total_assets"), "equity": s.get("equity"),
            "equity_ratio_pct": _r(s["equity_ratio"] * 100.0) if fm._ok(s.get("equity_ratio")) else None,
            "cf_operating": cfo, "cf_investing": cfi, "cf_financing": s.get("cf_financing"),
            "simple_fcf": cfo + cfi if fm._ok(cfo) and fm._ok(cfi) else None,
            # 営業CF ÷ 純利益（5.4）: net profit <= 0 -> not computed
            "ocf_to_profit": _r(cfo / npf) if fm._ok(cfo) and fm._ok(npf) and npf > 0 else None,
            "eps": s.get("eps"), "dividend_annual": s.get("dividend_annual"),
        })
    forecast = None
    fc = {item: fm.current_forecast(f.records, item) for item in fm.ITEMS}
    target = min((v["fy_end"] for v in fc.values() if v), default=None)
    if target is not None:
        vals = {item: (v["value"] if v and v["fy_end"] == target else None) for item, v in fc.items()}
        sales, op = vals["net_sales"], vals["operating_profit"]
        latest = max((v for v in fc.values() if v and v["fy_end"] == target), key=lambda v: v["disclosed_date"])
        forecast = {"kind": "forecast", "fy_end": target, "fiscal_year": fm.fy_label(target),
                    "disclosed_date": latest["disclosed_date"], "disclosure_no": latest["disclosure_no"],
                    **vals,
                    "operating_margin": _r(op / sales * 100.0) if fm._ok(op) and fm._ok(sales) and sales > 0
                    else None}
    return {"rows": rows, "forecast": forecast,
            "notes": [render("fin.note.annual_forecast"), render("fin.note.standard_change"),
                      render("fin.note.equity_ratio"), render("fin.note.cf_quarterly")]}


def _quarterly(f: Fins) -> dict:
    rows = f.get("quarterly")[-8:]
    out = []
    for q in rows:
        out.append({**{k: q[k] for k in ("fy_end", "fiscal_year", "quarter", "period_type", "period_end",
                                           "disclosed_date", "disclosure_no", "prev_disclosure_no",
                                           "accounting_standard", "special_factors_note", "irregular_year")},
                    "label": f"{q['fiscal_year']} Q{q['quarter']}",
                    "values": q["values"],
                    "reasons": {k: _reason(v) for k, v in q["reasons"].items()}})
    return {"rows": out, "notes": [render("fin.note.quarterly"), render("fin.note.q4")]}


def _progress(f: Fins) -> dict:
    p = f.get("progress")
    cfg = fm.rules()["alerts"]
    base = {"threshold_pt": cfg["progress_deviation_pt"], "provisional": bool(cfg["progress_deviation_provisional"]),
            "notes": [render("fin.note.progress"),
                      render("fin.note.progress_provisional", th=num(cfg["progress_deviation_pt"], 0))]}
    if p.get("state") != "ok":
        return {**base, "state": p.get("state", "no_data"), "disclosed_date": p.get("disclosed_date"),
                "primary_item": None, "items": {}}
    items = {}
    for item, it in p["items"].items():
        a, pat = it["actual"], it["pattern"]
        items[item] = {
            "label": _item(item),
            "actual_pct": _r(a["pct"], 2), "cumulative": a["cumulative"], "forecast": a["forecast"],
            "forecast_disclosed_date": a["forecast_disclosed_date"],
            "actual_reason": _reason(a["reason"]),
            "pattern_state": pat["state"], "pattern_avg_pct": _r(pat["average"], 2),
            "pattern_min_pct": _r(pat["min"], 2), "pattern_max_pct": _r(pat["max"], 2),
            "pattern_years": [{**y, "pct": _r(y["pct"], 2), "pct_vs_forecast_then": _r(y["pct_vs_forecast_then"], 2),
                               "reason": _reason(y["reason"])} for y in pat["years"]],
            "pattern_n_valid": pat["n_valid"], "pattern_required": pat["required"],
            "deviation_pt": _r(it["deviation_pt"], 2),
            "beyond_threshold": it["deviation_pt"] is not None
            and abs(fm._r6(it["deviation_pt"])) >= cfg["progress_deviation_pt"],
        }
    return {**base, "state": "ok", "period_type": p["period_type"], "period_label": _period(p["period_type"]),
            "fy_end": p["fy_end"], "fiscal_year": p["fiscal_year"], "disclosed_date": p["disclosed_date"],
            "disclosure_no": p["disclosure_no"], "primary_item": _progress_item(p), "items": items}


def _type_text(t: dict) -> str:
    th = num(t["threshold_pct"], 0)
    c = t["counts"]
    if t["type"] == "insufficient":
        return render("fin.type.insufficient", n=c["n"], m=t["m"], min=fm.rules()["forecast_type"]["min_years"])
    if t["type"] == "volatile":
        return render("fin.type.volatile", n=c["n"], above=c["above"], below=c["below"], within=c["within"], th=th)
    return render(f"fin.type.{t['type']}", n=c["n"], m=t["m"], th=th)


def _revisions(f: Fins) -> dict:
    hist = f.get("history")
    t = fm.classify_forecast_type(hist)
    years = []
    for h in sorted(hist, key=lambda h: h["fy_end"], reverse=True):
        years.append({
            "fy_end": h["fy_end"], "fiscal_year": h["fiscal_year"], "completed": h["completed"],
            "initial_from_prior_fy_statement": h["initial_from_prior_fy_statement"],
            "timeline": [{**e, "rates": {k: _r(v, 2) for k, v in e["rates"].items()}} for e in h["timeline"]],
            "revisions": [{**e, "rate": _r(e["rate"], 2), "item_label": _item(e["item"])} for e in h["revisions"]],
            "actual": h["actual"],
            "landing_pct_operating_profit": _r(h["landing_pct_operating_profit"], 2),
        })
    c = t["counts"]
    return {
        "years": years,
        "type": {"type": t["type"], "n": c["n"], "m": t["m"], "counts": c, "text": _type_text(t),
                 "counts_text": render("fin.type.counts", n=c["n"], above=c["above"], below=c["below"],
                                       within=c["within"], upward=c["upward_revision_years"],
                                       th=num(t["threshold_pct"], 0)),
                 "years": [{**y, "landing_pct": _r(y["landing_pct"], 2)} for y in t["years"]],
                 "threshold_pct": t["threshold_pct"]},
        "notes": [render("fin.note.forecast_type"), render("fin.note.forecast_initial")],
    }


def _pattern_label(p: dict | None) -> str | None:
    if not p:
        return None
    sym = {"+": "＋", "-": "−", "0": "0"}
    return render("fin.pattern", **{k: sym[v] for k, v in p.items()})


def _cf(f: Fins) -> dict:
    cf = f.get("cf")
    notes = [render("fin.note.ocf_ratio"), render("fin.note.ocf_guide"), render("fin.note.fcf"),
             render("fin.note.cf_quarterly")]
    if cf.get("state") != "ok":
        return {"state": "no_data", "notes": notes}
    return {**{k: cf[k] for k in ("state", "fy_end", "fiscal_year", "disclosed_date", "disclosure_no",
                                  "cf_operating", "cf_investing", "cf_financing", "profit", "simple_fcf",
                                  "pattern", "guideposts")},
            "ocf_to_profit": _r(cf["ocf_to_profit"]), "ocf_to_profit_reason": _reason(cf["ocf_to_profit_reason"]),
            "simple_fcf_reason": _reason(cf["simple_fcf_reason"]),
            "pattern_label": _pattern_label(cf["pattern"]), "notes": notes}


def _yoy(f: Fins) -> dict:
    y = f.get("yoy")
    if y.get("state") == "no_data":
        return {"state": "no_data"}
    out = {k: y[k] for k in ("state", "period_type", "fy_end", "disclosed_date", "disclosure_no")}
    out["period_label"] = _period(y["period_type"])
    out["reason"] = _reason(y.get("reason"))
    out["items"] = {k: {**v, "pct": _r(v["pct"], 2)} for k, v in y["items"].items()}
    return out


def financials_payload(conn: sqlite3.Connection, code: str) -> dict:
    f = Fins(conn, code)
    if f.empty:
        return {"code": f.code, "state": "no_data", "as_of": None, "alerts": alerts(f),
                "correction_log": [], "source_note": ENDPOINT_NOTE}
    eq = f.get("equity_ratio")
    return {
        "code": f.code, "state": "ok", "as_of": f.as_of,
        "latest_statement": None if f.latest is None else {
            "disclosed_date": f.latest["disclosed_date"], "disclosure_no": f.latest["disclosure_no"],
            "period_type": f.latest["period_type"], "fy_end": f.latest["fy_end"],
            "doc_type": f.latest["doc_type"]},
        "annual": _annual(f),
        "quarterly": _quarterly(f),
        "yoy": _yoy(f),
        "progress": _progress(f),
        "revisions": _revisions(f),
        "cf": _cf(f),
        "equity_ratio": {**eq, "reason": _reason(eq.get("reason")),
                         "current_pct": _r(eq.get("current_pct"), 2), "prior_pct": _r(eq.get("prior_pct"), 2),
                         "diff_pt": _r(eq.get("diff_pt"), 2)} if eq.get("state") == "ok" else {"state": "no_data"},
        "alerts": alerts(f),
        "correction_log": revision_log(conn, f.code),
        "source_note": ENDPOINT_NOTE,
    }


# --------------------------------------------------------------------------- /valuation

def _metric(value: float | None, reason: str | None, as_of: str | None, digits: int = 2, **extra) -> dict:
    return {"value": _r(value, digits) if value is not None else None, "available": value is not None,
            "reason": reason, "as_of": as_of, **extra}


class Valuation:
    def __init__(self, conn: sqlite3.Connection, f: Fins):
        self.f = f
        self.bars = fm.load_valuation_bars(conn, f.code)
        self.series = fm.valuation_series(f.records, self.bars)
        self.as_of = None if self.bars.empty else str(self.bars["date"].iloc[-1])
        self.last = None if self.series.empty else self.series.iloc[-1]

    def percentile(self, col: str) -> dict:
        if self.series.empty or self.as_of is None:
            return {"state": "insufficient", "n": 0}
        p = fm.percentile_position(self.series[col], self.series["date"], self.as_of)
        return {**p, **{k: _r(p[k], 4) for k in ("current", "percentile", "p25", "p50", "p75")}}


def _val_reason(v: Valuation, need: str) -> str | None:
    if v.bars.empty:
        return render("fin.reason.no_bars")
    if v.last is None or pd.isna(v.last["market_cap_yen"]):
        return render("fin.reason.no_market_cap")
    if v.f.empty:
        return render("fin.reason.no_data")
    if need == "per":
        fp = fm.current_forecast(v.f.records, "profit", v.as_of)
        return render("fin.reason.no_forecast") if fp is None else render("fin.reason.forecast_nonpositive")
    if need == "pbr":
        return render("fin.reason.no_equity") if pd.isna(v.last["equity"]) else render("fin.reason.equity_nonpositive")
    if need == "yield":
        return render("fin.reason.no_forecast")
    return None


def valuation_payload(conn: sqlite3.Connection, code: str) -> dict:
    f = Fins(conn, code)
    v = Valuation(conn, f)
    as_of = v.as_of
    last = v.last
    notes = [render("fin.note.pbr_estimated"), render("fin.note.per_mcap"), render("fin.note.market_cap_unit"),
             render("fin.note.point_in_time"), render("fin.note.percentile_position"), render("fin.note.no_peer"),
             render("fin.note.dividend_split")]
    mcap = None if last is None or pd.isna(last["market_cap_yen"]) else float(last["market_cap_yen"])

    def val(col):
        return None if last is None or pd.isna(last[col]) else float(last[col])

    state_t = fm._state_at(f.records, as_of) if as_of else {"forecast_profit": None, "equity": None, "dividend": None}
    per_v, pbr_v, yld_v = val("per"), val("pbr"), val("dividend_yield")
    fp, eq, dv = state_t["forecast_profit"], state_t["equity"], state_t["dividend"]
    shares = val("est_shares")
    per = _metric(per_v, None if per_v is not None else _val_reason(v, "per"), as_of,
                  inputs={"market_cap_yen": mcap, "forecast_profit": fp and fp["value"],
                          "forecast_fy_end": fp and fp["fy_end"],
                          "forecast_disclosed_date": fp and fp["disclosed_date"]},
                  percentile=v.percentile("per"), estimated=False)
    pbr = _metric(pbr_v, None if pbr_v is not None else _val_reason(v, "pbr"), as_of,
                  inputs={"market_cap_yen": mcap, "equity": eq and eq["value"],
                          "equity_disclosed_date": eq and eq["disclosed_date"],
                          "estimated_shares": _r(shares, 0),
                          "estimated_bps": _r(eq["value"] / shares, 2) if eq and shares else None,
                          "close_unadjusted": val("close")},
                  percentile=v.percentile("pbr"), estimated=True)
    yld = _metric(yld_v, None if yld_v is not None else _val_reason(v, "yield"), as_of,
                  inputs={"dividend_forecast": dv and dv["value"], "dividend_disclosed_date": dv and dv["disclosed_date"],
                          "adjustment_multiplier": _r(val("adj_mult"), 6), "close_unadjusted": val("close")},
                  percentile=v.percentile("dividend_yield"), estimated=False)
    roe_a = fm.roe_actual(f.stmts)
    roe_f_val = None
    roe_f_reason = None
    fp_now = fm.current_forecast(f.records, "profit")
    eq_latest = next((s for s in reversed(sorted(f.stmts, key=fm.disclosure_key)) if fm._ok(s.get("equity"))), None)
    if fp_now is None:
        roe_f_reason = "no_forecast"
    elif eq_latest is None:
        roe_f_reason = "no_equity"
    elif eq_latest["equity"] <= 0:
        roe_f_reason = "equity_nonpositive"
    else:
        roe_f_val = fp_now["value"] / eq_latest["equity"] * 100.0
    pa, pf = fm.payout_actual(f.stmts), fm.payout_forecast(f.records)
    roe_notes = [render("fin.note.roe_forecast")]
    if roe_a.get("opening_missing") and roe_a.get("value") is not None:
        roe_notes.insert(0, render("fin.note.roe_opening"))
    window = v.series
    if as_of is not None and not window.empty:
        start = (pd.Timestamp(as_of) - pd.DateOffset(years=fm.rules()["valuation"]["percentile_years"])).date().isoformat()
        window = window[window["date"] > start]
    return {
        "code": f.code,
        "state": "ok" if as_of else "no_data",
        "as_of": as_of,
        "market_cap": {"value_yen": mcap, "as_of": as_of, "unit_yen": fm.rules()["market_cap"]["unit_yen"],
                       "unit_estimated": fm.rules()["market_cap"]["unit_status"] == "estimated"},
        "estimated_shares": {"value": _r(shares, 0), "estimated": True, "as_of": as_of},
        "per": per, "pbr": pbr, "dividend_yield": yld,
        "roe": {
            "actual": _metric(roe_a.get("value"), _reason(roe_a.get("reason")), roe_a.get("disclosed_date"),
                              fiscal_year=roe_a.get("fiscal_year"), opening_missing=roe_a.get("opening_missing"),
                              inputs={k: roe_a.get(k) for k in ("profit", "equity_start", "equity_end", "equity_avg")}),
            "forecast": _metric(roe_f_val, _reason(roe_f_reason), fp_now and fp_now["disclosed_date"], estimated=True,
                                inputs={"forecast_profit": fp_now and fp_now["value"],
                                        "equity": eq_latest and eq_latest["equity"],
                                        "equity_disclosed_date": eq_latest and eq_latest["disclosed_date"]}),
            "notes": roe_notes,
        },
        "payout": {
            "actual": _metric(pa.get("value"), _reason(pa.get("reason")), pa.get("disclosed_date"),
                              fiscal_year=pa.get("fiscal_year"),
                              inputs={"dividend": pa.get("dividend"), "eps": pa.get("eps")}),
            "forecast": _metric(pf.get("value"), _reason(pf.get("reason")), pf.get("disclosed_date"),
                                fiscal_year=pf.get("fiscal_year"),
                                inputs={"dividend": pf.get("dividend"), "eps": pf.get("eps")}),
        },
        "series": {
            "dates": window["date"].tolist() if not window.empty else [],
            "per": [_r(x, 3) for x in window["per"]] if not window.empty else [],
            "pbr": [_r(x, 3) for x in window["pbr"]] if not window.empty else [],
            "dividend_yield": [_r(x, 3) for x in window["dividend_yield"]] if not window.empty else [],
        },
        "notes": notes,
        "source_note": ENDPOINT_NOTE,
    }


# --------------------------------------------------------------------------- /cards/business

def _performance_card(f: Fins) -> list[dict]:
    if f.empty:
        return [_sentence("no_fins", "fin.card.no_fins", None, False)]
    s = []
    y = f.get("yoy")
    if y.get("state") == "ok":
        it = y["items"]
        if it["operating_profit"]["kind"] == "not_disclosed" and it["profit"]["kind"] != "not_disclosed":
            s.append(_sentence("yoy", "fin.card.yoy_np", y["disclosed_date"], target="fin-annual",
                               period=_period(y["period_type"]), sales=_yoy_phrase(it["net_sales"]),
                               np=_yoy_phrase(it["profit"]), fiscal_year=fm.fy_label(y["fy_end"])))
        else:
            s.append(_sentence("yoy", "fin.card.yoy", y["disclosed_date"], target="fin-annual",
                               period=_period(y["period_type"]), sales=_yoy_phrase(it["net_sales"]),
                               op=_yoy_phrase(it["operating_profit"]), fiscal_year=fm.fy_label(y["fy_end"])))
    elif y.get("state") == "unavailable":
        s.append(_sentence("yoy", "fin.card.yoy_unavailable", y["disclosed_date"], False, "fin-annual",
                           period=_period(y["period_type"]), reason=_reason(y["reason"]),
                           fiscal_year=fm.fy_label(y["fy_end"])))
    p = f.get("progress")
    if p.get("state") == "not_applicable":
        s.append(_sentence("progress", "fin.card.progress_fy", p["disclosed_date"], False, "fin-progress"))
    elif p.get("state") == "ok":
        item = _progress_item(p)
        it = p["items"][item]
        a, pat = it["actual"], it["pattern"]
        common = {"period": _period(p["period_type"]), "item": _item(item)}
        if a["pct"] is None:
            s.append(_sentence("progress", "fin.card.progress_unavailable", p["disclosed_date"], False,
                               "fin-progress", reason=_reason(a["reason"]), **common))
        elif pat["state"] != "ok":
            s.append(_sentence("progress", "fin.card.progress_insufficient", p["disclosed_date"], True,
                               "fin-progress", pct=num(a["pct"]), n=pat["n_valid"], **common))
        else:
            s.append(_sentence("progress", "fin.card.progress", p["disclosed_date"], True, "fin-progress",
                               pct=num(a["pct"]), min=num(pat["min"]), max=num(pat["max"]),
                               avg=num(pat["average"]), dev=signed(it["deviation_pt"]), **common))
    year, events = f.current_revisions()
    if year is None:
        s.append(_sentence("revision", "fin.card.revision_nodata", f.as_of, False, "fin-revisions"))
    elif events:
        last_date = events[-1]["disclosed_date"]
        same = [e for e in events if e["disclosed_date"] == last_date]
        s.append(_sentence("revision", "fin.card.revision_latest", last_date, True, "fin-revisions",
                           date=last_date, fiscal_year=year["fiscal_year"],
                           changes="、".join(_change_phrase(e) for e in same)))
    else:
        first = year["timeline"][0]["disclosed_date"] if year["timeline"] else f.as_of
        s.append(_sentence("revision", "fin.card.revision_none", f.as_of, True, "fin-revisions",
                           fiscal_year=year["fiscal_year"], date=first))
    return s[:4]


def _health_card(f: Fins) -> list[dict]:
    if f.empty:
        return [_sentence("no_fins", "fin.card.no_fins", None, False)]
    s = []
    e = f.get("equity_ratio")
    if e.get("state") == "ok":
        common = {"cur": num(e["current_pct"]), "period": _period_end(e["period_type"]),
                  "fiscal_year": fm.fy_label(e["fy_end"])}
        if e["diff_pt"] is not None:
            s.append(_sentence("equity_ratio", "fin.card.equity_ratio", e["disclosed_date"], True, "fin-cf",
                               diff=signed(e["diff_pt"]), **common))
        else:
            s.append(_sentence("equity_ratio", "fin.card.equity_ratio_noprior", e["disclosed_date"], True, "fin-cf",
                               reason=_reason(e["reason"]), **common))
    else:
        s.append(_sentence("equity_ratio", "fin.card.equity_ratio_unavailable", f.as_of, False, "fin-cf"))
    cf = f.get("cf")
    if cf.get("state") == "ok":
        if cf["ocf_to_profit"] is not None:
            s.append(_sentence("ocf_ratio", "fin.card.ocf_ratio", cf["disclosed_date"], True, "fin-cf",
                               fiscal_year=cf["fiscal_year"], ratio=num(cf["ocf_to_profit"], 2)))
        else:
            s.append(_sentence("ocf_ratio", "fin.card.ocf_ratio_unavailable", cf["disclosed_date"], False, "fin-cf",
                               fiscal_year=cf["fiscal_year"], reason=_reason(cf["ocf_to_profit_reason"])))
        if cf["simple_fcf"] is not None:
            v = cf["simple_fcf"]
            sign = "fin.sign.plus" if v > 0 else ("fin.sign.minus" if v < 0 else "fin.sign.zero")
            s.append(_sentence("fcf", "fin.card.fcf", cf["disclosed_date"], True, "fin-cf",
                               fiscal_year=cf["fiscal_year"], fcf=yen(v), sign=render(sign)))
        else:
            s.append(_sentence("fcf", "fin.card.fcf_unavailable", cf["disclosed_date"], False, "fin-cf",
                               fiscal_year=cf["fiscal_year"]))
        if cf["pattern"]:
            s.append(_sentence("cf_pattern", "fin.card.cf_pattern", cf["disclosed_date"], True, "fin-cf",
                               fiscal_year=cf["fiscal_year"], pattern=_pattern_label(cf["pattern"])))
    return s[:4]


def _valuation_sentences(conn: sqlite3.Connection, f: Fins, v: dict | None = None) -> list[dict]:
    v = v or valuation_payload(conn, f.code)
    as_of = v["as_of"]
    s = []
    for key, label in (("per", "per"), ("pbr", "pbr")):
        m = v[key]
        if m["value"] is None:
            s.append(_sentence(key, f"fin.card.{label}_unavailable", as_of, False, "fin-valuation",
                               reason=m["reason"] or "—"))
            continue
        p = m["percentile"]
        if p.get("state") == "ok":
            s.append(_sentence(key, f"fin.card.{label}", as_of, True, "fin-valuation", value=num(m["value"], 2),
                               years=p["years"], pct=num(p["percentile"], 0), n=f"{p['n']:,}",
                               **{"from": p["from"][:7], "to": p["to"][:7]}))
        else:
            s.append(_sentence(key, f"fin.card.{label}_insufficient", as_of, True, "fin-valuation",
                               value=num(m["value"], 2), n=f"{p.get('n', 0):,}",
                               min=f"{fm.rules()['valuation']['min_observations']:,}"))
    pf = v["payout"]["forecast"]
    payout = (render("fin.payout.forecast", pct=num(pf["value"], 0)) if pf["value"] is not None
              else render("fin.payout.forecast_unavailable", reason=pf["reason"] or "—"))
    y = v["dividend_yield"]
    if y["value"] is not None:
        s.append(_sentence("dividend", "fin.card.dividend", as_of, True, "fin-valuation",
                           yield_=num(y["value"], 2), payout=payout))
    else:
        s.append(_sentence("dividend", "fin.card.dividend_unavailable", as_of, False, "fin-valuation",
                           reason=y["reason"] or "—", payout=payout))
    return s


def business_cards(conn: sqlite3.Connection, code: str) -> dict:
    from .macro import card_sentences

    f = Fins(conn, code)
    v = valuation_payload(conn, f.code)
    valuation = _valuation_sentences(conn, f, v)
    # The valuation card is "バリュエーション・マクロ位置" (要件 2.3). `parts.macro`
    # holds the macro sentences (PER next to the current rate levels, FX vs
    # the 短観 rate); `sentences` = PER, PBR, dividend + the first macro line,
    # capped at 4 lines. The sector tags travel as chips, not sentences.
    m = card_sentences(conn, f.code, v["per"]["value"], v["per"]["as_of"])
    parts = {"valuation": valuation, "macro": m["sentences"]}
    return {
        "code": f.code,
        "fins_as_of": f.as_of,
        "cards": [
            {"key": "performance", "title": "業績方向", "sentences": _performance_card(f)},
            {"key": "health", "title": "財務健全性", "sentences": _health_card(f)},
            {"key": "valuation", "title": "バリュエーション・マクロ位置",
             "sentences": (parts["valuation"][:3] + parts["macro"])[:4], "parts": parts,
             "macro_state": m["state"], "chips": m["chips"], "chips_note": render("macro.card.chips_note"),
             "caution": m["caution"], "notes": [render("macro.fx.tankan_note")]},
        ],
    }


# --------------------------------------------------------------------------- glossary values

def glossary_values(conn: sqlite3.Connection, code: str) -> dict[str, str]:
    """Formatted values for the Phase 2 glossary formulas. Missing -> absent
    (the glossary then shows the generic formula)."""
    out: dict[str, str] = {}
    f = Fins(conn, code)
    if f.empty:
        return out
    v = valuation_payload(conn, code)
    if v["market_cap"]["value_yen"] is not None:
        out["mcap"] = yen(v["market_cap"]["value_yen"])
        out["val_as_of"] = v["as_of"]
    per, pbr = v["per"], v["pbr"]
    if per["value"] is not None:
        out.update(fnp=yen(per["inputs"]["forecast_profit"]), per=num(per["value"], 2))
    if pbr["value"] is not None:
        out.update(equity=yen(pbr["inputs"]["equity"]), pbr=num(pbr["value"], 2))
    ra = v["roe"]["actual"]
    if ra["value"] is not None and not ra["opening_missing"]:
        out.update(roe_np=yen(ra["inputs"]["profit"]), roe_eq0=yen(ra["inputs"]["equity_start"]),
                   roe_eq1=yen(ra["inputs"]["equity_end"]), roe=num(ra["value"], 1), roe_fy=ra["fiscal_year"])
    p = f.get("progress")
    if p.get("state") == "ok":
        a = p["items"]["operating_profit"]["actual"]
        if a["pct"] is not None:
            out.update(prog_period=_period(p["period_type"]), prog_cum=yen(a["cumulative"]),
                       prog_fc=yen(a["forecast"]), prog_pct=num(a["pct"]), prog_as_of=p["disclosed_date"])
    cf = f.get("cf")
    if cf.get("state") == "ok":
        out["cf_fy"] = cf["fiscal_year"]
        if cf["ocf_to_profit"] is not None:
            out.update(cfo=yen(cf["cf_operating"]), cf_np=yen(cf["profit"]), ocf_ratio=num(cf["ocf_to_profit"], 2))
        if cf["simple_fcf"] is not None:
            out.update(cfo=yen(cf["cf_operating"]), cfi=yen(cf["cf_investing"]), fcf=yen(cf["simple_fcf"]))
    pf = v["payout"]["forecast"]
    if pf["value"] is not None:
        out.update(pf_div=num(pf["inputs"]["dividend"], 2), pf_eps=num(pf["inputs"]["eps"], 2),
                   pf_pct=num(pf["value"]), pf_date=pf["as_of"])
    e = f.get("equity_ratio")
    if e.get("state") == "ok" and e["diff_pt"] is not None:
        out.update(eqr=num(e["current_pct"]), eqr_prev=num(e["prior_pct"]), eqr_diff=signed(e["diff_pt"]),
                   eqr_period=f"{fm.fy_label(e['fy_end'])} {_period_end(e['period_type'])}")
    _, events = f.current_revisions()
    hist = f.get("history")
    all_events = [ev for h in hist for ev in h["revisions"] if ev["item"] in fm.ITEMS and ev["rate"] is not None]
    ev = (events[-1] if events and events[-1]["rate"] is not None else (all_events[-1] if all_events else None))
    if ev is not None:
        out.update(rv_new=yen(ev["new"]), rv_old=yen(ev["old"]), rv_rate=signed(ev["rate"]),
                   rv_date=ev["disclosed_date"], rv_item=f"{fm.fy_label(ev['fy_end'])}の通期{_item(ev['item'])}予想")
    # 予想配当利回り
    y = v["dividend_yield"]
    if y["value"] is not None and y["inputs"]["close_unadjusted"]:
        out.update(dy_div=num(y["inputs"]["dividend_forecast"], 2), dy_mult=num(y["inputs"]["adjustment_multiplier"], 4),
                   dy_close=num(y["inputs"]["close_unadjusted"], 1), dy=num(y["value"], 2))
    # 営業利益率（直近の通期実績）
    last_fy = next((s for s in reversed(f.stmts) if s["period_type"] == "FY"), None)
    if last_fy is not None and fm._ok(last_fy.get("operating_profit")) and fm._ok(last_fy.get("net_sales")) \
            and last_fy["net_sales"] > 0:
        out.update(om_op=yen(last_fy["operating_profit"]), om_sales=yen(last_fy["net_sales"]),
                   om=num(last_fy["operating_profit"] / last_fy["net_sales"] * 100.0), om_fy=fm.fy_label(last_fy["fy_end"]))
    # 四半期単独値（直近の Q2〜Q4 で売上高の差分が算出できたもの）
    by_no = {s["disclosure_no"]: s for s in f.stmts}
    for q in reversed(f.get("quarterly")):
        cur, prev = by_no.get(q["disclosure_no"]), by_no.get(q.get("prev_disclosure_no"))
        val = q["values"].get("net_sales")
        if q["quarter"] >= 2 and cur and prev and fm._ok(val) and fm._ok(cur.get("net_sales")) \
                and fm._ok(prev.get("net_sales")):
            out.update(q_label=f"{q['fiscal_year']} Q{q['quarter']}", q_period=_period(q["period_type"]),
                       q_cum=yen(cur["net_sales"]), q_prev=yen(prev["net_sales"]), q_val=yen(val))
            break
    # 前年同期比（売上高）
    yo = f.get("yoy")
    if yo.get("state") == "ok" and yo["items"]["net_sales"]["kind"] == "rate":
        s_ = yo["items"]["net_sales"]
        out.update(yoy_period=_period(yo["period_type"]), yoy_cur=yen(s_["current"]), yoy_prior=yen(s_["prior"]),
                   yoy_pct=signed(s_["pct"]), yoy_as_of=yo["disclosed_date"])
    # 自社過去レンジ内の位置（予想PER）
    pc = per["percentile"]
    if per["value"] is not None and pc.get("state") == "ok":
        out.update(pc_value=num(pc["current"], 2), pc_le=f"{round(pc['percentile'] * pc['n'] / 100):,}",
                   pc_n=f"{pc['n']:,}", pc_pct=num(pc["percentile"]), pc_from=pc["from"], pc_to=pc["to"])
    # キャッシュフロー3区分
    if cf.get("state") == "ok" and all(fm._ok(cf[k]) for k in ("cf_operating", "cf_investing", "cf_financing")):
        out.update(cfo=yen(cf["cf_operating"]), cfi=yen(cf["cf_investing"]), cff=yen(cf["cf_financing"]))
    # 予想の型
    t = fm.classify_forecast_type(hist)
    c = t["counts"]
    if c["n"] > 0:
        out.update(ft_n=str(c["n"]), ft_above=str(c["above"]), ft_below=str(c["below"]), ft_within=str(c["within"]),
                   ft_th=num(t["threshold_pct"], 0), ft_text=_type_text(t))
    return out
