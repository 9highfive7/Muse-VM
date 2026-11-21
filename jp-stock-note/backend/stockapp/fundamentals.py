"""Phase 2 calculations (要件 5.3 業績系・5.4 財務/CF系・5.5 バリュエーション系).

Pure functions over the stored financial-summary rows (``load_records``) and
daily bars. Nothing here calls J-Quants or renders text.

Conventions
* Every value that cannot be computed from the summary data is ``None`` with a
  reason string. Nothing is filled with 0 and nothing is estimated silently;
  values that rest on an estimate carry ``estimated: True``.
* Fiscal years are identified by their end date (``fy_end``). A fiscal year
  is *regular* when it spans 12 months; a fiscal-year-end change produces an
  irregular year, which is excluded wherever a 12-month basis is required.
* Disclosures are ordered by (disclosed_date, disclosed_time, disclosure_no).
  "Known at day t" means disclosed_date <= t (end-of-day knowledge).
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

RULES_PATH = Path(__file__).resolve().parent / "content" / "fin_rules.json"

PERIODS = ("1Q", "2Q", "3Q", "FY")
PERIOD_ORDER = {p: i for i, p in enumerate(PERIODS, 1)}
ITEMS = ("net_sales", "operating_profit", "profit")
FORECAST_ITEMS = ("net_sales", "operating_profit", "profit", "eps", "dividend")


@lru_cache(maxsize=1)
def rules() -> dict:
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _ok(v: Any) -> bool:
    return v is not None and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))


def _r6(v: float) -> float:
    """Round before threshold comparisons so 0.45 − 0.50 compares as −5.0pt."""
    return round(float(v), 6)


def disclosure_key(r: dict) -> tuple:
    return (r.get("disclosed_date") or "", r.get("disclosed_time") or "", r.get("disclosure_no") or "")


# --------------------------------------------------------------------------- loading

def load_records(conn: sqlite3.Connection, code: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM fins_summary WHERE code = ?", (code,)).fetchall()
    recs = []
    for r in rows:
        d = dict(r)
        d.pop("raw_json", None)
        if d.get("doc_kind") is None:   # rows inserted without the Phase 2 parser
            from .fins import parse_doc_type

            d.update(parse_doc_type(d.get("doc_type")))
        recs.append(d)
    return sorted(recs, key=disclosure_key)


def months(fy_start: str | None, fy_end: str | None) -> int | None:
    if not (fy_start and fy_end):
        return None
    s, e = date.fromisoformat(fy_start), date.fromisoformat(fy_end)
    return (e.year - s.year) * 12 + e.month - s.month + 1


def is_regular(fy_start: str | None, fy_end: str | None) -> bool:
    return months(fy_start, fy_end) == 12


def fy_label(fy_end: str | None) -> str:
    if not fy_end:
        return "—"
    d = date.fromisoformat(fy_end)
    return f"{d.year}年{d.month}月期"


# --------------------------------------------------------------------------- statements

def statements(records: Iterable[dict]) -> list[dict]:
    """Financial-statement disclosures, one per (fy_end, period) — the latest
    disclosure wins — ordered by fiscal year and period."""
    best: dict[tuple, dict] = {}
    for r in records:
        if r.get("doc_kind") != "statement" or r.get("period_type") not in PERIOD_ORDER or not r.get("fy_end"):
            continue
        key = (r["fy_end"], r["period_type"])
        if key not in best or disclosure_key(r) >= disclosure_key(best[key]):
            best[key] = r
    return sorted(best.values(), key=lambda r: (r["fy_end"], PERIOD_ORDER[r["period_type"]]))


def by_fiscal_year(stmts: list[dict]) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for s in stmts:
        out.setdefault(s["fy_end"], {})[s["period_type"]] = s
    return out


def latest_statement(stmts: list[dict]) -> dict | None:
    return stmts[-1] if stmts else None


def previous_fiscal_year(stmts: list[dict], fy_end: str) -> str | None:
    earlier = sorted({s["fy_end"] for s in stmts if s["fy_end"] < fy_end})
    return earlier[-1] if earlier else None


def _year_gap_ok(prev_end: str, cur_end: str) -> bool:
    """The previous fiscal year really is the year before (ends 11–13 months earlier)."""
    p, c = date.fromisoformat(prev_end), date.fromisoformat(cur_end)
    gap = (c.year - p.year) * 12 + c.month - p.month
    return 11 <= gap <= 13


def prior_same_period(stmts: list[dict], cur: dict) -> tuple[dict | None, str | None]:
    """The same period of the previous fiscal year, or (None, reason)."""
    if not is_regular(cur.get("fy_start"), cur.get("fy_end")):
        return None, "irregular_current"
    prev_fy = previous_fiscal_year(stmts, cur["fy_end"])
    if prev_fy is None or not _year_gap_ok(prev_fy, cur["fy_end"]):
        return None, "no_prior"
    prev = by_fiscal_year(stmts).get(prev_fy, {}).get(cur["period_type"])
    if prev is None:
        return None, "no_prior"
    if not is_regular(prev.get("fy_start"), prev.get("fy_end")):
        return None, "irregular_prior"
    if prev.get("accounting_standard") != cur.get("accounting_standard"):
        return None, "standard_changed"
    if prev.get("consolidation") != cur.get("consolidation"):
        return None, "consolidation_changed"
    return prev, None


# --------------------------------------------------------------------------- 5.3 quarterly standalone

def quarterly_standalone(stmts: list[dict]) -> list[dict]:
    """Q1 = 1Q cumulative; Qk = kQ − (k−1)Q (k = 2, 3); Q4 = FY − 3Q.
    Conditions: same fiscal year, same accounting standard, same consolidation
    basis, previous cumulative present; FY of an irregular (non-12-month) year
    is not converted. Otherwise each item is None with a reason code."""
    out = []
    for fy_end, periods in sorted(by_fiscal_year(stmts).items()):
        for k, pt in enumerate(PERIODS, 1):
            cur = periods.get(pt)
            if cur is None:
                continue
            prev_pt = PERIODS[k - 2] if k > 1 else None
            prev = periods.get(prev_pt) if prev_pt else None
            reason = None
            if pt == "FY" and not is_regular(cur.get("fy_start"), fy_end):
                reason = "irregular_year"
            elif prev_pt and prev is None:
                reason = "prev_missing"
            elif prev is not None and prev.get("fy_start") != cur.get("fy_start"):
                reason = "fiscal_year_differs"
            elif prev is not None and prev.get("accounting_standard") != cur.get("accounting_standard"):
                reason = "standard_changed"
            elif prev is not None and prev.get("consolidation") != cur.get("consolidation"):
                reason = "consolidation_changed"
            values: dict[str, float | None] = {}
            reasons: dict[str, str | None] = {}
            for item in ITEMS:
                c = cur.get(item)
                p = prev.get(item) if prev is not None else 0.0
                if reason:
                    values[item], reasons[item] = None, reason
                elif not _ok(c):
                    values[item], reasons[item] = None, "not_disclosed"
                elif not _ok(p):
                    values[item], reasons[item] = None, "prev_not_disclosed"
                else:
                    values[item], reasons[item] = c - p, None
            out.append({
                "fy_end": fy_end,
                "fy_start": cur.get("fy_start"),
                "fiscal_year": fy_label(fy_end),
                "quarter": k,
                "period_type": pt,
                "period_end": cur.get("period_end"),
                "disclosed_date": cur.get("disclosed_date"),
                "disclosure_no": cur.get("disclosure_no"),
                "prev_disclosure_no": prev.get("disclosure_no") if prev is not None else None,
                "accounting_standard": cur.get("accounting_standard"),
                "values": values,
                "reasons": reasons,
                "special_factors_note": pt == "FY",
                "irregular_year": not is_regular(cur.get("fy_start"), fy_end),
            })
    return out


# --------------------------------------------------------------------------- 5.3 YoY

def yoy_change(cur: float | None, prior: float | None) -> dict:
    """(cur − prior) ÷ |prior| × 100. prior <= 0: no rate, describe the facts."""
    if not _ok(cur) or not _ok(prior):
        return {"current": cur, "prior": prior, "pct": None, "kind": "not_disclosed"}
    if prior <= 0:
        kind = "turned_positive" if cur > 0 else "nonpositive_both"
        return {"current": cur, "prior": prior, "pct": None, "kind": kind}
    return {"current": cur, "prior": prior, "pct": (cur - prior) / abs(prior) * 100.0, "kind": "rate"}


def latest_yoy(stmts: list[dict]) -> dict:
    cur = latest_statement(stmts)
    if cur is None:
        return {"state": "no_data"}
    prev, reason = prior_same_period(stmts, cur)
    base = {"period_type": cur["period_type"], "fy_end": cur["fy_end"], "disclosed_date": cur["disclosed_date"],
            "disclosure_no": cur["disclosure_no"]}
    if prev is None:
        return {**base, "state": "unavailable", "reason": reason,
                "items": {i: {"current": cur.get(i), "prior": None, "pct": None, "kind": "unavailable"}
                          for i in ITEMS}}
    return {**base, "state": "ok", "prior_disclosure_no": prev["disclosure_no"],
            "prior_fy_end": prev["fy_end"],
            "items": {i: yoy_change(cur.get(i), prev.get(i)) for i in ITEMS}}


# --------------------------------------------------------------------------- forecasts

def forecast_observations(records: Iterable[dict]) -> list[dict]:
    """Every disclosed full-year forecast, keyed by the fiscal year it is for.
    kQ statements and forecast revisions: current-year forecast fields.
    FY statements: next-year forecast fields (= the next year's 期初予想)."""
    obs = []
    for r in records:
        kind = r.get("doc_kind")
        src = None
        if kind == "statement" and r.get("period_type") == "FY":
            if r.get("next_fy_end"):
                src = ("next_year", r.get("next_fy_start"), r.get("next_fy_end"), {
                    "net_sales": r.get("next_forecast_net_sales"),
                    "operating_profit": r.get("next_forecast_operating_profit"),
                    "profit": r.get("next_forecast_profit"),
                    "eps": r.get("next_forecast_eps"),
                    "dividend": r.get("next_forecast_dividend_annual")})
        elif kind in ("statement", "forecast_revision", "dividend_revision") and r.get("fy_end"):
            src = ("current_year", r.get("fy_start"), r.get("fy_end"), {
                "net_sales": r.get("forecast_net_sales"),
                "operating_profit": r.get("forecast_operating_profit"),
                "profit": r.get("forecast_profit"),
                "eps": r.get("forecast_eps"),
                "dividend": r.get("forecast_dividend_annual")})
        if src is None:
            continue
        source, fy_start, fy_end, values = src
        if not any(_ok(v) for v in values.values()):
            continue
        obs.append({"fy_end": fy_end, "fy_start": fy_start, "source": source,
                    "disclosed_date": r.get("disclosed_date"), "disclosed_time": r.get("disclosed_time"),
                    "disclosure_no": r.get("disclosure_no"), "doc_type": r.get("doc_type"),
                    "doc_kind": kind, "values": values})
    return sorted(obs, key=disclosure_key)


def forecast_as_of(obs: list[dict], fy_end: str, item: str, upto: tuple | None = None) -> dict | None:
    """Latest non-null forecast of ``item`` for ``fy_end`` disclosed at or before ``upto``."""
    best = None
    for o in obs:
        if o["fy_end"] != fy_end or not _ok(o["values"].get(item)):
            continue
        if upto is not None and disclosure_key(o) > upto:
            continue
        best = o
    return None if best is None else {"value": best["values"][item], "disclosed_date": best["disclosed_date"],
                                      "disclosure_no": best["disclosure_no"], "source": best["source"]}


def current_forecast(records: list[dict], item: str, t: str | None = None) -> dict | None:
    """今期の通期予想 as known at day ``t`` (None = everything stored): the
    fiscal year after the latest FY actual disclosed by t, latest value."""
    known = [r for r in records if t is None or (r.get("disclosed_date") or "") <= t]
    fy_actuals = [r["fy_end"] for r in statements(known) if r["period_type"] == "FY"]
    last_actual = max(fy_actuals) if fy_actuals else None
    obs = [o for o in forecast_observations(known) if _ok(o["values"].get(item))
           and (last_actual is None or o["fy_end"] > last_actual)]
    if not obs:
        return None
    target = min(o["fy_end"] for o in obs)
    out = forecast_as_of(obs, target, item)
    return {**out, "fy_end": target} if out else None


# --------------------------------------------------------------------------- 5.3 progress

def progress(stmts: list[dict], obs: list[dict], past_years: int | None = None) -> dict:
    """進捗率（今期）= kQ累計 ÷ その開示時点の通期予想 × 100（分母 <= 0 は算出不可）
    進捗率（過去パターン）= 過去3年度の kQ累計実績 ÷ 同年度の通期実績 × 100
    乖離 = 今期 − 過去3年の平均（ポイント）。過去が2年分以下なら比較不足。"""
    past_years = past_years or rules()["progress"]["past_years"]
    cur = latest_statement(stmts)
    if cur is None:
        return {"state": "no_data"}
    if cur["period_type"] == "FY":
        return {"state": "not_applicable", "period_type": "FY", "fy_end": cur["fy_end"],
                "disclosed_date": cur["disclosed_date"]}
    pt = cur["period_type"]
    years = by_fiscal_year(stmts)
    prior_fys = sorted(fy for fy in years if fy < cur["fy_end"] and "FY" in years[fy])[-past_years:][::-1]
    items = {}
    for item in ITEMS:
        f = forecast_as_of(obs, cur["fy_end"], item, disclosure_key(cur))
        c = cur.get(item)
        actual = {"cumulative": c, "forecast": f["value"] if f else None,
                  "forecast_disclosed_date": f["disclosed_date"] if f else None,
                  "forecast_disclosure_no": f["disclosure_no"] if f else None, "pct": None, "reason": None}
        if not _ok(c):
            actual["reason"] = "not_disclosed"
        elif f is None:
            actual["reason"] = "no_forecast"
        elif f["value"] <= 0:
            actual["reason"] = "forecast_nonpositive"
        else:
            actual["pct"] = c / f["value"] * 100.0
        past = []
        for fy in prior_fys:
            q, full = years[fy].get(pt), years[fy].get("FY")
            row = {"fy_end": fy, "fiscal_year": fy_label(fy), "cumulative": q.get(item) if q else None,
                   "full_year": full.get(item) if full else None, "pct": None, "reason": None,
                   "pct_vs_forecast_then": None}
            if not is_regular(full.get("fy_start"), fy):
                row["reason"] = "irregular_year"
            elif q is None:
                row["reason"] = "prev_missing"
            elif (q.get("accounting_standard"), q.get("consolidation")) != (
                    full.get("accounting_standard"), full.get("consolidation")):
                row["reason"] = "standard_changed"
            elif not _ok(row["cumulative"]) or not _ok(row["full_year"]):
                row["reason"] = "not_disclosed"
            elif row["full_year"] <= 0:
                row["reason"] = "full_year_nonpositive"
            else:
                row["pct"] = row["cumulative"] / row["full_year"] * 100.0
            if q is not None and _ok(row["cumulative"]):
                ft = forecast_as_of(obs, fy, item, disclosure_key(q))
                if ft and ft["value"] > 0:
                    row["pct_vs_forecast_then"] = row["cumulative"] / ft["value"] * 100.0
            past.append(row)
        valid = [p["pct"] for p in past if p["pct"] is not None]
        pattern = {"years": past, "n_valid": len(valid), "required": past_years,
                   "average": None, "min": None, "max": None, "state": "insufficient"}
        if len(valid) >= past_years:
            pattern.update(average=sum(valid) / len(valid), min=min(valid), max=max(valid), state="ok")
        deviation = None
        if actual["pct"] is not None and pattern["average"] is not None:
            deviation = actual["pct"] - pattern["average"]
        items[item] = {"actual": actual, "pattern": pattern, "deviation_pt": deviation}
    return {"state": "ok", "period_type": pt, "fy_end": cur["fy_end"], "fiscal_year": fy_label(cur["fy_end"]),
            "disclosed_date": cur["disclosed_date"], "disclosure_no": cur["disclosure_no"], "items": items}


# --------------------------------------------------------------------------- 5.3 revisions + type

def revision_rate(new: float, old: float) -> float | None:
    """(新予想 − 旧予想) ÷ |旧予想| × 100; old == 0 -> not computable."""
    return None if old == 0 else (new - old) / abs(old) * 100.0


def revision_history(stmts: list[dict], obs: list[dict]) -> list[dict]:
    """Per fiscal year: 期初予想 → 各修正 → 実績. Forecasts of the same fiscal
    year are ordered by disclosure; a change from the previous disclosed value
    of an item is a revision."""
    years = by_fiscal_year(stmts)
    fys = sorted({o["fy_end"] for o in obs} | {fy for fy in years if "FY" in years[fy]})
    out = []
    for fy in fys:
        seq = [o for o in obs if o["fy_end"] == fy]
        last: dict[str, float] = {}
        initial: dict[str, dict] = {}
        timeline, events = [], []
        for o in seq:
            entry = {"disclosed_date": o["disclosed_date"], "disclosure_no": o["disclosure_no"],
                     "doc_type": o["doc_type"], "source": o["source"], "values": {}, "rates": {},
                     "kind": "forecast"}
            changed = False
            for item in ITEMS + ("dividend",):
                v = o["values"].get(item)
                if not _ok(v):
                    continue
                entry["values"][item] = v
                if item not in last:
                    initial[item] = {"value": v, "disclosed_date": o["disclosed_date"],
                                     "disclosure_no": o["disclosure_no"], "source": o["source"]}
                elif v != last[item]:
                    rate = revision_rate(v, last[item])
                    entry["rates"][item] = rate
                    changed = True
                    events.append({"fy_end": fy, "item": item, "disclosed_date": o["disclosed_date"],
                                   "disclosure_no": o["disclosure_no"], "old": last[item], "new": v, "rate": rate})
                last[item] = v
            if not timeline:
                entry["kind"] = "initial"
            elif changed:
                entry["kind"] = "revision"
            else:
                entry["kind"] = "unchanged"
            timeline.append(entry)
        actual_stmt = years.get(fy, {}).get("FY")
        actual = None
        if actual_stmt is not None:
            actual = {"disclosed_date": actual_stmt["disclosed_date"], "disclosure_no": actual_stmt["disclosure_no"],
                      "values": {i: actual_stmt.get(i) for i in ITEMS}}
        landing = None
        op0 = initial.get("operating_profit")
        if actual and op0 and _ok(actual["values"]["operating_profit"]) and op0["value"] != 0:
            landing = (actual["values"]["operating_profit"] - op0["value"]) / abs(op0["value"]) * 100.0
        out.append({
            "fy_end": fy, "fiscal_year": fy_label(fy), "completed": actual is not None,
            "initial": initial,
            "initial_from_prior_fy_statement": bool(seq) and seq[0]["source"] == "next_year",
            "timeline": timeline, "revisions": events, "actual": actual,
            "landing_pct_operating_profit": landing,
            "upward_revision_operating_profit": any(e["item"] == "operating_profit" and (e["rate"] or 0) > 0
                                                    for e in events),
            "downward_revision_operating_profit": any(e["item"] == "operating_profit" and (e["rate"] or 0) < 0
                                                      for e in events),
        })
    return out


def classify_forecast_type(history: list[dict]) -> dict:
    """予想の型 (要件 5.3), over completed years N (max 5, latest first) whose
    operating-profit landing rate is computable."""
    cfg = rules()["forecast_type"]
    th, min_n, max_n = cfg["landing_threshold_pct"], cfg["min_years"], cfg["max_years"]
    done = [h for h in history if h["completed"] and h["landing_pct_operating_profit"] is not None]
    done = sorted(done, key=lambda h: h["fy_end"])[-max_n:]
    n = len(done)
    landings = [_r6(h["landing_pct_operating_profit"]) for h in done]
    above = sum(1 for x in landings if x > th)
    below = sum(1 for x in landings if x < -th)
    within = sum(1 for x in landings if -th <= x <= th)
    upward = sum(1 for h in done if h["upward_revision_operating_profit"])
    counts = {"n": n, "above": above, "below": below, "within": within, "upward_revision_years": upward}
    years = [{"fy_end": h["fy_end"], "fiscal_year": h["fiscal_year"],
              "landing_pct": h["landing_pct_operating_profit"],
              "upward_revision": h["upward_revision_operating_profit"]} for h in done]
    if n < min_n:
        return {"type": "insufficient", "m": n, "counts": counts, "years": years, "threshold_pct": th}
    if above * 2 > n and upward * 2 > n:
        t, m = "conservative_upward", above
    elif below * 2 > n:
        t, m = "aggressive_downward", below
    elif within == n:
        t, m = "stable", n
    else:
        t, m = "volatile", max(above, below, within)
    return {"type": t, "m": m, "counts": counts, "years": years, "threshold_pct": th}


# --------------------------------------------------------------------------- 5.4 CF / balance

def _sign(v: float) -> str:
    return "+" if v > 0 else ("-" if v < 0 else "0")


def cf_metrics(stmts: list[dict]) -> dict:
    """Latest FY: 営業CF ÷ 純利益 (純利益 <= 0 は算出不可), 簡易FCF = 営業CF + 投資CF,
    3区分の符号パターン."""
    fys = [s for s in stmts if s["period_type"] == "FY"]
    if not fys:
        return {"state": "no_data"}
    s = fys[-1]
    cfo, cfi, cff, npf = s.get("cf_operating"), s.get("cf_investing"), s.get("cf_financing"), s.get("profit")
    ratio, ratio_reason = None, None
    if not _ok(cfo) or not _ok(npf):
        ratio_reason = "not_disclosed"
    elif npf <= 0:
        ratio_reason = "profit_nonpositive"
    else:
        ratio = cfo / npf
    fcf = cfo + cfi if _ok(cfo) and _ok(cfi) else None
    signs = None
    if all(_ok(v) for v in (cfo, cfi, cff)):
        signs = {"operating": _sign(cfo), "investing": _sign(cfi), "financing": _sign(cff)}
    return {"state": "ok", "fy_end": s["fy_end"], "fiscal_year": fy_label(s["fy_end"]),
            "disclosed_date": s["disclosed_date"], "disclosure_no": s["disclosure_no"],
            "cf_operating": cfo, "cf_investing": cfi, "cf_financing": cff, "profit": npf,
            "ocf_to_profit": ratio, "ocf_to_profit_reason": ratio_reason,
            "simple_fcf": fcf, "simple_fcf_reason": None if fcf is not None else "not_disclosed",
            "pattern": signs,
            "guideposts": {"reference": rules()["cf"]["ocf_ratio_reference"], "check": rules()["cf"]["ocf_ratio_check"]}}


def equity_ratio_change(stmts: list[dict]) -> dict:
    with_ratio = [s for s in stmts if _ok(s.get("equity_ratio"))]
    if not with_ratio:
        return {"state": "no_data"}
    cur = with_ratio[-1]
    prev, reason = prior_same_period(stmts, cur)
    out = {"period_type": cur["period_type"], "fy_end": cur["fy_end"], "disclosed_date": cur["disclosed_date"],
           "disclosure_no": cur["disclosure_no"], "current_pct": cur["equity_ratio"] * 100.0,
           "prior_pct": None, "diff_pt": None, "reason": reason}
    if prev is not None and _ok(prev.get("equity_ratio")):
        out.update(prior_pct=prev["equity_ratio"] * 100.0,
                   diff_pt=(cur["equity_ratio"] - prev["equity_ratio"]) * 100.0, reason=None,
                   prior_disclosed_date=prev["disclosed_date"])
    elif prev is not None:
        out["reason"] = "not_disclosed"
    return {"state": "ok", **out}


# --------------------------------------------------------------------------- 5.5 valuation

def roe_actual(stmts: list[dict]) -> dict:
    fys = [s for s in stmts if s["period_type"] == "FY"]
    if not fys:
        return {"value": None, "reason": "no_fy"}
    s = fys[-1]
    npf, eq1 = s.get("profit"), s.get("equity")
    prev_fy = previous_fiscal_year(fys, s["fy_end"])
    prev = by_fiscal_year(fys).get(prev_fy, {}).get("FY") if prev_fy else None
    eq0 = prev.get("equity") if prev else None
    base = {"fy_end": s["fy_end"], "fiscal_year": fy_label(s["fy_end"]), "disclosed_date": s["disclosed_date"],
            "profit": npf, "equity_end": eq1, "equity_start": eq0, "opening_missing": not _ok(eq0)}
    if not _ok(npf) or not _ok(eq1):
        return {**base, "value": None, "reason": "not_disclosed"}
    avg = (eq0 + eq1) / 2 if _ok(eq0) else eq1
    if avg <= 0:
        return {**base, "value": None, "reason": "equity_nonpositive"}
    return {**base, "value": npf / avg * 100.0, "reason": None, "equity_avg": avg}


def payout_actual(stmts: list[dict]) -> dict:
    fys = [s for s in stmts if s["period_type"] == "FY"]
    if not fys:
        return {"value": None, "reason": "no_fy"}
    s = fys[-1]
    div, eps = s.get("dividend_annual"), s.get("eps")
    base = {"fy_end": s["fy_end"], "fiscal_year": fy_label(s["fy_end"]), "disclosed_date": s["disclosed_date"],
            "disclosure_no": s["disclosure_no"], "dividend": div, "eps": eps}
    if not _ok(div) or not _ok(eps):
        return {**base, "value": None, "reason": "not_disclosed"}
    if eps <= 0:
        return {**base, "value": None, "reason": "eps_nonpositive"}
    return {**base, "value": div / eps * 100.0, "reason": None}


def payout_forecast(records: list[dict]) -> dict:
    """予想配当 ÷ 予想EPS × 100 within the same disclosure (latest one carrying both)."""
    div = current_forecast(records, "dividend")
    eps = current_forecast(records, "eps")
    target = (div or eps or {}).get("fy_end")
    if target is None:
        return {"value": None, "reason": "no_forecast"}
    obs = [o for o in forecast_observations(records) if o["fy_end"] == target
           and _ok(o["values"].get("dividend")) and _ok(o["values"].get("eps"))]
    if not obs:
        return {"value": None, "reason": "no_forecast", "fy_end": target}
    o = obs[-1]
    d, e = o["values"]["dividend"], o["values"]["eps"]
    base = {"fy_end": target, "fiscal_year": fy_label(target), "disclosed_date": o["disclosed_date"],
            "disclosure_no": o["disclosure_no"], "dividend": d, "eps": e}
    if e <= 0:
        return {**base, "value": None, "reason": "eps_nonpositive"}
    return {**base, "value": d / e * 100.0, "reason": None}


def load_valuation_bars(conn: sqlite3.Connection, code: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT date, close, adj_factor, market_cap FROM daily_bars WHERE code = ? AND close IS NOT NULL"
        " ORDER BY date", conn, params=(code,))


def _state_at(records: list[dict], t: str) -> dict:
    known = [r for r in records if (r.get("disclosed_date") or "") <= t]
    st = statements(known)
    eq = next((s for s in reversed(sorted(st, key=disclosure_key)) if _ok(s.get("equity"))), None)
    return {"forecast_profit": current_forecast(records, "profit", t),
            "dividend": current_forecast(records, "dividend", t),
            "equity": None if eq is None else {"value": eq["equity"], "disclosed_date": eq["disclosed_date"],
                                               "disclosure_no": eq["disclosure_no"]}}


def valuation_series(records: list[dict], bars: pd.DataFrame, unit_yen: float | None = None) -> pd.DataFrame:
    """Daily 予想PER / PBR(推定) / 予想配当利回り using, on each day t, only
    numbers disclosed on or before t (要件 3.6). Market-cap based (要件 5.5)."""
    unit_yen = unit_yen or rules()["market_cap"]["unit_yen"]
    cols = ["date", "close", "market_cap_yen", "per", "pbr", "dividend_yield", "forecast_profit", "equity",
            "dividend", "adj_mult", "est_shares"]
    if bars.empty:
        return pd.DataFrame(columns=cols)
    df = bars.copy().reset_index(drop=True)
    df["market_cap_yen"] = df["market_cap"] * unit_yen
    factor = df["adj_factor"].fillna(1.0).astype(float)
    cum = factor.cumprod().to_numpy()
    dates = df["date"].to_numpy(dtype=object)
    events = sorted({r["disclosed_date"] for r in records if r.get("disclosed_date")})
    states = [_state_at(records, d) for d in events]
    ev = np.array(events, dtype=object)
    pos = np.searchsorted(ev, dates, side="right") - 1 if len(ev) else np.full(len(df), -1)
    fp, eq, dv, mult = [], [], [], []
    for i, p in enumerate(pos):
        s = states[p] if p >= 0 else {"forecast_profit": None, "dividend": None, "equity": None}
        fp.append(s["forecast_profit"]["value"] if s["forecast_profit"] else np.nan)
        eq.append(s["equity"]["value"] if s["equity"] else np.nan)
        if s["dividend"]:
            d0 = s["dividend"]["disclosed_date"]
            j = int(np.searchsorted(dates, d0, side="right")) - 1   # last bar on/before d0
            base = cum[j] if j >= 0 else 1.0
            dv.append(s["dividend"]["value"])
            mult.append(cum[i] / base)                               # A(d0, t) = Π_{d0<d<=t} 調整係数
        else:
            dv.append(np.nan)
            mult.append(np.nan)
    df["forecast_profit"] = fp
    df["equity"] = eq
    df["dividend"] = dv
    df["adj_mult"] = mult
    mc = df["market_cap_yen"].astype(float)
    df["per"] = np.where(df["forecast_profit"] > 0, mc / df["forecast_profit"], np.nan)
    df["pbr"] = np.where(df["equity"] > 0, mc / df["equity"], np.nan)
    df["dividend_yield"] = np.where(df["close"] > 0, df["dividend"] * df["adj_mult"] / df["close"] * 100.0, np.nan)
    df["est_shares"] = np.where(df["close"] > 0, mc / df["close"], np.nan)
    return df[cols]


def percentile_position(values: pd.Series, dates: pd.Series, as_of: str, years: int | None = None,
                        min_obs: int | None = None) -> dict:
    """P = #{x_i <= x_now} ÷ N × 100 over the last ``years`` of computable days;
    N < 250 -> 観測不足 (not shown). Also the 25/50/75 percentiles of the window."""
    cfg = rules()["valuation"]
    years = years or cfg["percentile_years"]
    min_obs = min_obs or cfg["min_observations"]
    start = (pd.Timestamp(as_of) - pd.DateOffset(years=years)).date().isoformat()
    mask = (dates > start) & (dates <= as_of)
    window = pd.DataFrame({"date": dates[mask], "x": values[mask]}).dropna()
    n = len(window)
    cur_row = window[window["date"] == as_of]
    out = {"n": n, "min_observations": min_obs, "years": years,
           "from": window["date"].iloc[0] if n else None, "to": window["date"].iloc[-1] if n else None,
           "current": None, "percentile": None, "p25": None, "p50": None, "p75": None, "state": "insufficient"}
    if cur_row.empty:
        out["state"] = "current_unavailable"
        return out
    x_now = float(cur_row["x"].iloc[-1])
    out["current"] = x_now
    if n < min_obs:
        return out
    xs = window["x"].to_numpy(dtype=float)
    out.update(state="ok", percentile=float((xs <= x_now).sum()) / n * 100.0,
               p25=float(np.percentile(xs, 25)), p50=float(np.percentile(xs, 50)), p75=float(np.percentile(xs, 75)))
    return out
