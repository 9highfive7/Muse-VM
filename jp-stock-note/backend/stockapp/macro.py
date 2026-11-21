"""Phase 3 macro read side (要件 3.8 / 4.5 / 2.3): /api/macro,
/api/stocks/{code}/macro, the macro sentences of the valuation card and the
macro glossary values. DB only (never calls FRED).

Calculations
* Headline series: latest value, as-of, changes over 20 / 60 / 250
  *observations* (business days for daily series, months for monthly ones)
  and the position within the trailing 250-observation window
  (P = #{x_i ≤ x_now} ÷ N × 100, the same definition as 要件 5.5; N < 250 ->
  not shown). Payloads always carry all three windows — never a 1-day change
  on its own.
* US 10y−2y spread: computed from DGS10 and DGS2 on dates where *both* have a
  value (holidays/missing on either side -> no spread that day). Used instead
  of FRED's T10Y2Y so the spread is consistent with the two stored legs.
* USD/JPY vs the 短観 assumed rate (app setting): gap in yen and %.
* Stale badge (要件 3.8): a daily series whose as-of is ≥5 business days older
  than the reference date (stock data / TOPIX) is emphasised; a monthly
  series when its month is ≥3 months before the reference month (its normal
  lag is about one month, so the 5-day rule would always fire).
* Failed fetch (要件 4.5): the last stored values stay and the item says
  「取得失敗・前回値を表示中」.

The tailwind/headwind checklist states, per factor, the condition under which
the sector's tags say "plus direction" and the one they say "minus
direction", next to the current 20/60 changes. It deliberately does *not*
say which side the present direction falls on, and carries no score
(要件 1.3 / 6.1「円安なので追い風」).
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from . import fred
from .app_settings import get_tankan
from .indicators import ma_order
from .queries import resolve_code
from .wording import num, render, signed

SENSITIVITY_PATH = Path(__file__).resolve().parent / "content" / "sector_sensitivity.json"
WINDOWS = (20, 60, 250)
RANGE_WINDOW = 250
HEADLINE = ("topix", "us10y", "us_spread", "usdjpy", "jp10y", "call_rate")
ITEM_SERIES = {"us10y": "DGS10", "us2y": "DGS2", "usdjpy": "DEXJPUS", "jp10y": "IRLTLT01JPM156N",
               "call_rate": "IRSTCI01JPM156N"}
HISTORY_POINTS = {"daily": 250, "monthly": 120}


def cfg() -> dict:
    return fred.config()


@lru_cache(maxsize=1)
def sensitivity() -> dict:
    return json.loads(SENSITIVITY_PATH.read_text(encoding="utf-8"))


def sectors() -> dict[str, dict]:
    return sensitivity()["sectors"]


def _r(v: Any, digits: int = 4) -> float | None:
    if v is None:
        return None
    f = float(v)
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, digits)


# --------------------------------------------------------------------------- calculations

def changes(points: list[tuple[str, float]], kind: str = "diff") -> dict[str, dict]:
    """Change over n observations for n in 20/60/250. ``kind='diff'`` gives
    the difference (rates: %ポイント, FX: 円); ``'pct'`` the rate of change
    R_n = C(t)/C(t−n) − 1 in % (TOPIX, 要件 5.2). Both are always included
    for 'pct' and FX ('change' = difference, 'change_pct' = %)."""
    out = {}
    for n in WINDOWS:
        if len(points) <= n:
            out[str(n)] = {"n": n, "available": False, "from_date": None, "from_value": None,
                           "change": None, "change_pct": None}
            continue
        d0, v0 = points[-1 - n]
        v1 = points[-1][1]
        pct = (v1 / v0 - 1.0) * 100.0 if v0 else None
        out[str(n)] = {"n": n, "available": True, "from_date": d0, "from_value": _r(v0, 6),
                       "change": _r(v1 - v0, 6), "change_pct": _r(pct, 6) if kind in ("pct", "fx") else None}
    return out


def range_position(points: list[tuple[str, float]], window: int = RANGE_WINDOW) -> dict:
    n = min(len(points), window)
    if n < window:
        return {"state": "insufficient", "n": n, "window": window}
    tail = points[-window:]
    vals = [v for _, v in tail]
    now = vals[-1]
    lo, hi = min(vals), max(vals)
    pct = sum(1 for v in vals if v <= now) / window * 100.0
    return {"state": "ok", "n": window, "window": window, "from": tail[0][0], "to": tail[-1][0],
            "min": _r(lo, 6), "max": _r(hi, 6), "percentile": _r(pct, 4),
            "range_pos": _r((now - lo) / (hi - lo) * 100.0, 4) if hi > lo else None}


def aligned_spread(long_pts: list[tuple[str, float]], short_pts: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """10y − 2y on common dates only (both values present)."""
    short = dict(short_pts)
    return [(d, v - short[d]) for d, v in long_pts if d in short]


def tankan_gap(usd: float | None, rate: float | None) -> dict:
    if usd is None or rate is None or rate <= 0:
        return {"available": False, "gap_yen": None, "gap_pct": None}
    gap = usd - rate
    return {"available": True, "gap_yen": round(gap, 2), "gap_pct": round(gap / rate * 100.0, 2),
            "gap_yen_raw": gap, "gap_pct_raw": gap / rate * 100.0}


def direction(change: float | None, kind: str) -> str | None:
    """'up' / 'down' / 'flat' against the configured flat band (第3階層に表示)."""
    if change is None:
        return None
    th = cfg()["direction"]
    band = {"rate": th["rate_flat_pt"], "fx": th["fx_flat_pct"], "topix": th["topix_flat_pct"]}[kind]
    if abs(change) < band:
        return "flat"
    return "up" if change > 0 else "down"


def business_days_between(conn: sqlite3.Connection, start: str, end: str) -> int:
    """Business days in (start, end]. Uses the J-Quants trading calendar when
    it covers the range, otherwise Mon–Fri."""
    if start >= end:
        return 0
    lo, hi = conn.execute("SELECT MIN(date), MAX(date) FROM trading_calendar").fetchone()
    if lo and hi and lo <= start and hi >= end:
        return conn.execute("SELECT COUNT(*) FROM trading_calendar WHERE is_business_day=1 AND date > ? AND date <= ?",
                            (start, end)).fetchone()[0]
    s = date.fromisoformat(start) + timedelta(days=1)
    e = date.fromisoformat(end) + timedelta(days=1)
    return int(np.busday_count(s, e))


def months_between(start: str, end: str) -> int:
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    return (e.year - s.year) * 12 + (e.month - s.month)


# --------------------------------------------------------------------------- items

def _fmt_change(c: dict, digits: int, pct: bool = False) -> str:
    if not c["available"]:
        return render("macro.change.unavailable")
    return signed(c["change_pct"] if pct else c["change"], digits)


def _month(d: str | None) -> str:
    return "—" if not d else render("macro.month", y=d[:4], m=str(int(d[5:7])))


def _stale(conn: sqlite3.Connection, frequency: str, as_of: str | None, ref: str | None) -> dict:
    if not as_of or not ref:
        return {"stale": False, "text": None, "lag": None}
    th = cfg()["stale"]
    if frequency == "monthly":
        lag = months_between(as_of, ref)
        st = lag >= th["monthly_months"]
        return {"stale": st, "lag": lag, "unit": "months",
                "text": render("macro.stale.monthly", ref=ref, n=lag) if st else None}
    lag = business_days_between(conn, as_of, ref)
    st = lag >= th["daily_business_days"]
    return {"stale": st, "lag": lag, "unit": "business_days",
            "text": render("macro.stale.daily", ref=ref, n=lag) if st else None}


def _fetch_block(conn: sqlite3.Connection, series_id: str) -> dict:
    s = fred.fetch_state(conn, series_id)
    failed = s["last_result"] not in (None, "ok")
    return {**s, "failed": failed,
            "status_text": render("macro.fetch_failed", last=s["last_success_at"] or "—") if failed else None}


def _base(key: str, d: dict, frequency: str) -> dict:
    return {"key": key, "title": d["title"], "short": d["short"], "unit": d["unit"],
            "change_unit": d.get("change_unit"), "frequency": frequency, "cadence_badge": d["cadence_badge"],
            "lag_note": d["lag_note"], "source": d["source"], "note": d.get("substitute_note")}


def _history(points: list[tuple[str, float]], frequency: str, digits: int = 4) -> dict:
    tail = points[-HISTORY_POINTS[frequency]:]
    return {"dates": [d for d, _ in tail], "values": [_r(v, digits) for _, v in tail],
            "step": frequency == "monthly"}


def series_item(conn: sqlite3.Connection, key: str, series_id: str, ref: str | None,
                points: list[tuple[str, float]] | None = None) -> dict:
    d = fred.series_defs()[series_id]
    freq = d["frequency"]
    pts = fred.observations(conn, series_id) if points is None else points
    kind = "fx" if key == "usdjpy" else "diff"
    item = _base(key, d, freq) | {"series_id": series_id, "source_url": f"https://fred.stlouisfed.org/series/{series_id}"}
    item["fetch"] = _fetch_block(conn, series_id)
    if not pts:
        item.update(state="no_data", value=None, as_of=None, as_of_month=None, changes=changes([], kind),
                    range_250=range_position([]), stale={"stale": False, "text": None, "lag": None},
                    facts=[{"key": "level", "text": render("macro.no_data", short=d["short"]), "available": False,
                            "as_of": None}], history=_history([], freq))
        if freq == "monthly":
            item["month_change"] = None
        return item
    as_of, value = pts[-1]
    ch = changes(pts, kind)
    item.update(state="ok", value=_r(value, 4), as_of=as_of,
                as_of_month=as_of[:7] if freq == "monthly" else None,
                changes=ch, range_250=range_position(pts), stale=_stale(conn, freq, as_of, ref),
                history=_history(pts, freq))
    facts = []
    if freq == "monthly":
        m1 = _r(pts[-1][1] - pts[-2][1], 6) if len(pts) >= 2 else None
        item["month_change"] = {"from_date": pts[-2][0] if len(pts) >= 2 else None, "change": m1}
        facts.append({"key": "level", "available": True, "as_of": as_of,
                      "text": render("macro.monthly.level", short=d["short"], value=num(value, 2),
                                     month=_month(as_of), m1=signed(m1, 2) if m1 is not None else
                                     render("macro.change.unavailable"),
                                     c20=_fmt_change(ch["20"], 2), c60=_fmt_change(ch["60"], 2),
                                     c250=_fmt_change(ch["250"], 2))})
    elif key == "usdjpy":
        facts.append({"key": "level", "available": True, "as_of": as_of,
                      "text": render("macro.fx.level", value=num(value, 2), c20=_fmt_change(ch["20"], 2),
                                     c60=_fmt_change(ch["60"], 2), c250=_fmt_change(ch["250"], 2),
                                     as_of=as_of)})
    else:
        facts.append({"key": "level", "available": True, "as_of": as_of,
                      "text": render("macro.rate.level", short=d["short"], value=num(value, 2),
                                     c20=_fmt_change(ch["20"], 2), c60=_fmt_change(ch["60"], 2),
                                     c250=_fmt_change(ch["250"], 2), as_of=as_of)})
    facts.append(_range_fact(item["range_250"], as_of, 2))
    item["facts"] = facts
    return item


def _range_fact(rp: dict, as_of: str | None, digits: int) -> dict:
    if rp["state"] != "ok":
        return {"key": "range", "available": False, "as_of": as_of,
                "text": render("macro.range_insufficient", n=rp["n"], window=rp["window"])}
    return {"key": "range", "available": True, "as_of": as_of,
            "text": render("macro.range", lo=num(rp["min"], digits), hi=num(rp["max"], digits),
                           pct=num(rp["percentile"], 0), n=rp["n"], **{"from": rp["from"], "to": rp["to"]})}


def spread_item(conn: sqlite3.Connection, ref: str | None) -> dict:
    d = cfg()["derived"]["US_SPREAD"]
    pts = aligned_spread(fred.observations(conn, "DGS10"), fred.observations(conn, "DGS2"))
    item = _base("us_spread", d, "daily") | {"series_id": None, "inputs": ["DGS10", "DGS2"],
                                              "source_url": "https://fred.stlouisfed.org/series/DGS10"}
    f10, f2 = _fetch_block(conn, "DGS10"), _fetch_block(conn, "DGS2")
    item["fetch"] = {"failed": f10["failed"] or f2["failed"],
                     "status_text": f10["status_text"] or f2["status_text"],
                     "last_success_at": min(filter(None, [f10["last_success_at"], f2["last_success_at"]]), default=None),
                     "last_result": f10["last_result"] if f10["failed"] else f2["last_result"]}
    if not pts:
        item.update(state="no_data", value=None, as_of=None, sign=None, changes=changes([]),
                    range_250=range_position([]), stale={"stale": False, "text": None, "lag": None},
                    history=_history([], "daily"),
                    facts=[{"key": "level", "text": render("macro.no_data", short=d["short"]), "available": False,
                            "as_of": None}])
        return item
    as_of, value = pts[-1]
    value = round(value, 6)
    ch = changes(pts)
    sign = "positive" if value > 0 else ("negative" if value < 0 else "zero")
    item.update(state="ok", value=_r(value, 4), as_of=as_of, sign=sign, changes=ch, range_250=range_position(pts),
                stale=_stale(conn, "daily", as_of, ref), history=_history(pts, "daily"),
                legs={"long": _r(dict(fred.observations(conn, "DGS10"))[as_of], 4),
                      "short": _r(dict(fred.observations(conn, "DGS2"))[as_of], 4)})
    item["facts"] = [
        {"key": "level", "available": True, "as_of": as_of,
         "text": render("macro.spread.level", value=signed(value, 2), sign=render(f"macro.spread.sign.{sign}"),
                        c20=_fmt_change(ch["20"], 2), c60=_fmt_change(ch["60"], 2), c250=_fmt_change(ch["250"], 2),
                        as_of=as_of)},
        _range_fact(item["range_250"], as_of, 2),
    ]
    return item


def topix_item(conn: sqlite3.Connection, ref: str | None) -> dict:
    d = cfg()["topix"]
    rows = conn.execute("SELECT date, close FROM topix_bars WHERE close IS NOT NULL ORDER BY date").fetchall()
    pts = [(r[0], float(r[1])) for r in rows]
    meta = conn.execute("SELECT * FROM series_meta WHERE source='jquants' AND series_id='topix_bars'").fetchone()
    failed = bool(meta and meta["last_result"] not in (None, "ok"))
    item = _base("topix", d, "daily") | {"series_id": "topix", "source_url": None}
    item["fetch"] = {"failed": failed, "last_success_at": meta["last_success_at"] if meta else None,
                     "last_result": meta["last_result"] if meta else None,
                     "status_text": render("macro.fetch_failed", last=(meta["last_success_at"] if meta else None) or "—")
                     if failed else None}
    if not pts:
        item.update(state="no_data", value=None, as_of=None, changes=changes([], "pct"), range_250=range_position([]),
                    ma=None, stale={"stale": False, "text": None, "lag": None}, history=_history([], "daily"),
                    facts=[{"key": "level", "text": render("macro.topix.no_data"), "available": False, "as_of": None}])
        return item
    as_of, value = pts[-1]
    ch = changes(pts, "pct")
    closes = [v for _, v in pts]
    mas = {n: (sum(closes[-n:]) / n if len(closes) >= n else None) for n in (25, 75, 200)}
    devs = {n: ((value - m) / m * 100.0 if m else None) for n, m in mas.items()}
    order = ma_order({n: m for n, m in mas.items()})
    item.update(state="ok", value=_r(value, 2), as_of=as_of, changes=ch, range_250=range_position(pts),
                stale=_stale(conn, "daily", as_of, ref), history=_history(pts, "daily", 2),
                ma={"sma": {str(n): _r(m, 4) for n, m in mas.items()},
                    "deviation_pct": {str(n): _r(v, 4) for n, v in devs.items()},
                    "order": order["order"], "order_kind": order["kind"]})
    facts = [{"key": "level", "available": True, "as_of": as_of,
              "text": render("macro.topix.level", value=num(value, 2), c20=_fmt_change(ch["20"], 1, True),
                             c60=_fmt_change(ch["60"], 1, True), c250=_fmt_change(ch["250"], 1, True), as_of=as_of)}]
    if order["order"] is None:
        facts.append({"key": "ma", "available": False, "as_of": as_of,
                      "text": render("macro.topix.ma_unavailable", as_of=as_of)})
    else:
        facts.append({"key": "ma", "available": True, "as_of": as_of,
                      "text": render("macro.topix.ma", dev25=signed(devs[25], 1), dev75=signed(devs[75], 1),
                                     dev200=signed(devs[200], 1), order="・".join(f"{n}日" for n in order["order"]),
                                     kind=render(f"order.{order['kind']}"), as_of=as_of)})
    facts.append(_range_fact(item["range_250"], as_of, 2))
    item["facts"] = facts
    return item


def reference_date(conn: sqlite3.Connection, code: str | None = None) -> tuple[str | None, str]:
    """Date the macro as-ofs are compared against (要件 3.8 基準日のずれ)."""
    if code:
        d = conn.execute("SELECT MAX(date) FROM daily_bars WHERE code=?", (resolve_code(code),)).fetchone()[0]
        if d:
            return d, "stock"
    d = conn.execute("SELECT MAX(date) FROM topix_bars").fetchone()[0]
    if d:
        return d, "topix"
    from .queries import today_jst

    return today_jst(), "today"


def macro_payload(conn: sqlite3.Connection, code: str | None = None) -> dict:
    ref, ref_kind = reference_date(conn, code)
    items = {
        "topix": topix_item(conn, ref),
        "us10y": series_item(conn, "us10y", "DGS10", ref),
        "us2y": series_item(conn, "us2y", "DGS2", ref),
        "us_spread": spread_item(conn, ref),
        "usdjpy": series_item(conn, "usdjpy", "DEXJPUS", ref),
        "jp10y": series_item(conn, "jp10y", "IRLTLT01JPM156N", ref),
        "call_rate": series_item(conn, "call_rate", "IRSTCI01JPM156N", ref),
    }
    tk = get_tankan(conn)
    fx = items["usdjpy"]
    gap = tankan_gap(fx["value"], tk["value"])
    fx["tankan"] = {"rate": tk["value"], "survey_label": tk["survey_label"], "updated_at": tk["updated_at"],
                    "origin": tk["origin"], **{k: gap[k] for k in ("available", "gap_yen", "gap_pct")},
                    "note": render("macro.fx.tankan_note")}
    if gap["available"]:
        fx["facts"].insert(1, {"key": "tankan", "available": True, "as_of": fx["as_of"],
                               "text": _tankan_text(fx["value"], tk, gap, fx["as_of"])})
    any_data = any(items[k]["state"] == "ok" for k in ITEM_SERIES)
    return {
        "state": "ok" if any_data else "no_data",
        "reference_date": ref,
        "reference_kind": ref_kind,
        "reference_label": render(f"macro.ref.{ref_kind}", ref=ref),
        "headline": list(HEADLINE),
        "items": items,
        "windows": list(WINDOWS),
        "thresholds": {k: v for k, v in cfg()["direction"].items() if not k.startswith("_")},
        "stale_rule": {k: v for k, v in cfg()["stale"].items() if not k.startswith("_")},
        "notes": [render("macro.note.windows"), render("macro.note.no_news"), render("macro.note.monthly_step"),
                  render("macro.note.forward_fill")],
        "source_note": render("macro.note.source"),
    }


def _tankan_text(usd: float, tk: dict, gap: dict, as_of: str) -> str:
    g = gap["gap_yen"]
    dir_key = "dir.above" if g > 0 else ("dir.below" if g < 0 else "dir.equal")
    return render("macro.fx.tankan", usd=num(usd, 2), label=tk["survey_label"], rate=num(tk["value"], 2),
                  gap=num(abs(g), 2), gap_pct=num(abs(gap["gap_pct"]), 2), dir=render(dir_key), as_of=as_of)


# --------------------------------------------------------------------------- sector profile

def sector_entry(code17: str | None, name17: str | None = None) -> tuple[str | None, dict | None]:
    if code17 and code17 in sectors():
        return code17, sectors()[code17]
    if name17:
        for k, v in sectors().items():
            if v["name"] == name17:
                return k, v
    return None, None


def sector_profile(code17: str | None, name17: str | None) -> dict:
    lab = sensitivity()["labels"]
    notes = sensitivity()["notes"]
    key, s = sector_entry(code17, name17)
    if s is None:
        return {"state": "unmapped", "sector17": {"code": code17, "name": name17},
                "text": render("macro.profile.unmapped", sector=name17 or code17 or "—"),
                "tags": [], "cycle": None, "caution": {"flag": False},
                "notes": [notes["general_tendency"]]}
    tags = []
    for cond in ("rate_up", "rate_down", "yen_weak", "yen_strong"):
        eff = s[cond]
        tags.append({"factor": cond, "condition": lab["condition"][cond], "effect": eff,
                     "effect_label": lab["effect"][eff],
                     "label": render("macro.tag", condition=lab["condition"][cond], effect=lab["effect_short"][eff])})
    caution = {"flag": bool(s.get("caution")), "label": sensitivity()["caution"]["label"] if s.get("caution") else None,
               "text": sensitivity()["caution"]["text"] if s.get("caution") else None,
               "reason": s.get("caution_reason")}
    return {"state": "ok", "sector17": {"code": key, "name": s["name"]},
            "cycle": {"value": s["cycle"], "label": lab["cycle"][s["cycle"]]},
            "tags": tags, "why": s["why"], "caution": caution,
            "notes": [notes["general_tendency"], notes["sector33"]]}


# --------------------------------------------------------------------------- checklist

DIR_LABEL_KIND = {"rate": "macro.dir.rate", "fx": "macro.dir.fx", "topix": "macro.dir.topix"}


def _dir_label(kind: str, d: str | None) -> str:
    return render("macro.change.unavailable") if d is None else render(f"{DIR_LABEL_KIND[kind]}.{d}")


def _conditions(s: dict, factor: str) -> tuple[list[str], list[str]]:
    """(plus-direction conditions, minus-direction conditions) from the tags."""
    plus, minus = [], []
    if factor == "cycle":
        cyc = s["cycle"]
        return [render(f"macro.check.cycle.{cyc}.plus")], [render(f"macro.check.cycle.{cyc}.minus")]
    conds = ("rate_up", "rate_down") if factor == "rates" else ("yen_weak", "yen_strong")
    for c in conds:
        if s[c] == "tailwind":
            plus.append(render(f"macro.check.cond.{c}"))
        elif s[c] == "headwind":
            minus.append(render(f"macro.check.cond.{c}"))
    none = render("macro.check.none", factor=render(f"macro.check.title.{factor}"))
    return plus or [none], minus or [none]


def _rate_current(items: dict) -> dict:
    us, jp = items["us10y"], items["jp10y"]
    facts, dirs = [], {}
    if us["state"] == "ok" and us["changes"]["60"]["available"]:
        c20, c60 = us["changes"]["20"]["change"], us["changes"]["60"]["change"]
        dirs["us10y"] = {"20": direction(c20, "rate"), "60": direction(c60, "rate")}
        facts.append(render("macro.check.cur.us10", c20=signed(c20, 2), d20=_dir_label("rate", dirs["us10y"]["20"]),
                            c60=signed(c60, 2), d60=_dir_label("rate", dirs["us10y"]["60"]), as_of=us["as_of"]))
    else:
        dirs["us10y"] = {"20": None, "60": None}
        facts.append(render("macro.check.cur.unavailable", short=us["short"]))
    pts = list(zip(jp["history"]["dates"], jp["history"]["values"]))
    if jp["state"] == "ok" and len(pts) >= 4:
        j1, j3 = pts[-1][1] - pts[-2][1], pts[-1][1] - pts[-4][1]
        # 20 / 60 business days ≈ 1 / 3 months for the monthly series
        dirs["jp10y"] = {"20": direction(j1, "rate"), "60": direction(j3, "rate"), "basis": "1m_3m"}
        facts.append(render("macro.check.cur.jp10", c1=signed(j1, 2), d1=_dir_label("rate", dirs["jp10y"]["20"]),
                            c3=signed(j3, 2), d3=_dir_label("rate", dirs["jp10y"]["60"]), month=_month(jp["as_of"])))
    else:
        dirs["jp10y"] = {"20": None, "60": None, "basis": "1m_3m"}
        facts.append(render("macro.check.cur.unavailable", short=jp["short"]))
    return {"facts": facts, "direction": dirs, "series": ["DGS10", "IRLTLT01JPM156N"],
            "as_of": {"us10y": us["as_of"], "jp10y": jp["as_of"]}}


def _fx_current(items: dict) -> dict:
    fx = items["usdjpy"]
    if fx["state"] != "ok" or not fx["changes"]["60"]["available"]:
        return {"facts": [render("macro.check.cur.unavailable", short=fx["short"])],
                "direction": {"usdjpy": {"20": None, "60": None}}, "series": ["DEXJPUS"], "as_of": {"usdjpy": fx["as_of"]}}
    ch = fx["changes"]
    dirs = {"20": direction(ch["20"]["change_pct"], "fx"), "60": direction(ch["60"]["change_pct"], "fx")}
    facts = [render("macro.check.cur.fx", c20=signed(ch["20"]["change"], 2), d20=_dir_label("fx", dirs["20"]),
                    c60=signed(ch["60"]["change"], 2), d60=_dir_label("fx", dirs["60"]), as_of=fx["as_of"])]
    tk = fx.get("tankan")
    if tk and tk["available"]:
        facts.append(_tankan_text(fx["value"], {"survey_label": tk["survey_label"], "value": tk["rate"]},
                                  {"gap_yen": tk["gap_yen"], "gap_pct": tk["gap_pct"]}, fx["as_of"]))
    return {"facts": facts, "direction": {"usdjpy": dirs}, "series": ["DEXJPUS"], "as_of": {"usdjpy": fx["as_of"]}}


def _cycle_current(items: dict) -> dict:
    t = items["topix"]
    if t["state"] != "ok" or not t["changes"]["60"]["available"]:
        return {"facts": [render("macro.check.cur.topix_unavailable")], "direction": {"topix": {"20": None, "60": None}},
                "series": ["topix"], "as_of": {"topix": t["as_of"]}}
    ch = t["changes"]
    dirs = {"20": direction(ch["20"]["change_pct"], "topix"), "60": direction(ch["60"]["change_pct"], "topix")}
    return {"facts": [render("macro.check.cur.topix", c20=signed(ch["20"]["change_pct"], 1),
                             d20=_dir_label("topix", dirs["20"]), c60=signed(ch["60"]["change_pct"], 1),
                             d60=_dir_label("topix", dirs["60"]), as_of=t["as_of"])],
            "direction": {"topix": dirs}, "series": ["topix"], "as_of": {"topix": t["as_of"]}}


def checklist(profile: dict, macro: dict) -> dict:
    notes = sensitivity()["notes"]
    out = {"rows": [], "notes": [notes["both_sides"], notes["general_tendency"], notes["no_peer"]],
           "thresholds": macro["thresholds"]}
    if profile["state"] != "ok":
        return out
    s = sectors()[profile["sector17"]["code"]]
    items = macro["items"]
    current = {"rates": _rate_current(items), "fx": _fx_current(items), "cycle": _cycle_current(items)}
    tag_keys = {"rates": ("rate_up", "rate_down"), "fx": ("yen_weak", "yen_strong"), "cycle": ()}
    why_key = {"rates": "rate", "fx": "fx", "cycle": "cycle"}
    for factor in ("rates", "fx", "cycle"):
        plus, minus = _conditions(s, factor)
        tags = {k: s[k] for k in tag_keys[factor]}
        relevant = factor == "cycle" or any(v != "neutral" for v in tags.values())
        out["rows"].append({
            "factor": factor,
            "title": render(f"macro.check.title.{factor}"),
            "tags": tags if factor != "cycle" else {"cycle": s["cycle"]},
            "tailwind_conditions": plus,
            "headwind_conditions": minus,
            "why": s["why"][why_key[factor]],
            "current": current[factor],
            "relevant": relevant,
            "caution": bool(s.get("caution")),
        })
    return out


def _context(conn: sqlite3.Connection, code5: str) -> dict:
    """PER (rate-sensitivity proxy) and equity ratio (leverage proxy) shown
    next to the sector tags (research-03: 33業種はPER・自己資本比率で補正して読む)."""
    from .financials import Fins, valuation_payload

    f = Fins(conn, code5)
    v = valuation_payload(conn, code5)
    per = v["per"]
    e = f.get("equity_ratio") if not f.empty else {"state": "no_data"}
    facts = []
    if per["value"] is not None:
        facts.append(render("macro.ctx.per", per=num(per["value"], 2), as_of=per["as_of"]))
    else:
        facts.append(render("macro.ctx.per_unavailable", reason=per["reason"] or "—"))
    if e.get("state") == "ok":
        facts.append(render("macro.ctx.eqr", eqr=num(e["current_pct"]), as_of=e["disclosed_date"]))
    else:
        facts.append(render("macro.ctx.eqr_unavailable"))
    return {"per": {"value": per["value"], "as_of": per["as_of"]},
            "equity_ratio": {"value": e.get("current_pct"), "as_of": e.get("disclosed_date")},
            "facts": facts, "note": render("macro.note.high_per")}


def stock_macro_payload(conn: sqlite3.Connection, code: str) -> dict:
    code5 = resolve_code(code)
    from .queries import stock_header

    head = stock_header(conn, code5)
    if head is None:
        return {"code": code5, "state": "not_found"}
    st = head["stock"]
    profile = sector_profile(st["sector17"]["code"], st["sector17"]["name"])
    m = macro_payload(conn, code5)
    return {
        "code": code5,
        "state": "ok",
        "sector17": st["sector17"],
        "sector33": st["sector33"],
        "profile": profile,
        "context": _context(conn, code5),
        "checklist": checklist(profile, m),
        "macro": m,
    }


# --------------------------------------------------------------------------- valuation card (要件 2.3)

def card_sentences(conn: sqlite3.Connection, code: str, per_value: float | None, per_as_of: str | None) -> dict:
    """Macro part of the 「バリュエーション・マクロ位置」 card: PER stated next
    to the current rate levels (facts only), FX vs the 短観 rate, and the
    sector tags as chips."""
    code5 = resolve_code(code)
    m = macro_payload(conn, code5)
    it = m["items"]
    us, jp, fx = it["us10y"], it["jp10y"], it["usdjpy"]
    sentences = []
    target = "macro-section"
    if us["state"] != "ok" and jp["state"] != "ok":
        checked = per_as_of or m["reference_date"]
        sentences.append({"key": "macro_rates", "text": render("macro.card.no_data", as_of=checked or "—"),
                          "as_of": checked, "available": False, "target": target})
    else:
        jp_txt = (render("macro.card.jp10", jp10=num(jp["value"], 2), month=_month(jp["as_of"]))
                  if jp["state"] == "ok" else render("macro.card.jp10_none"))
        us_txt = (render("macro.card.us10", us10=num(us["value"], 2), c60=_fmt_change(us["changes"]["60"], 2),
                         as_of=us["as_of"]) if us["state"] == "ok" else render("macro.card.us10_none"))
        if per_value is not None:
            text = render("macro.card.per_rates", per=num(per_value, 2), per_as_of=per_as_of, jp=jp_txt, us=us_txt)
        else:
            text = render("macro.card.rates", jp=jp_txt, us=us_txt, as_of=us["as_of"] or jp["as_of"] or "—")
        sentences.append({"key": "macro_rates", "text": text, "as_of": us["as_of"] or jp["as_of"], "available": True,
                          "target": target})
    if fx["state"] == "ok" and fx["tankan"]["available"]:
        sentences.append({"key": "macro_fx", "available": True, "as_of": fx["as_of"], "target": target,
                          "text": _tankan_text(fx["value"], {"survey_label": fx["tankan"]["survey_label"],
                                                             "value": fx["tankan"]["rate"]},
                                               {"gap_yen": fx["tankan"]["gap_yen"], "gap_pct": fx["tankan"]["gap_pct"]},
                                               fx["as_of"])})
    from .queries import stock_header

    head = stock_header(conn, code5)
    prof = sector_profile(head["stock"]["sector17"]["code"], head["stock"]["sector17"]["name"]) if head else None
    chips = []
    if prof and prof["state"] == "ok":
        chips = [prof["cycle"]["label"]] + [t["label"] for t in prof["tags"] if t["factor"] in ("rate_up", "yen_weak")]
    return {"state": "ok" if m["state"] == "ok" else "no_data", "sentences": sentences, "chips": chips,
            "caution": prof["caution"] if prof else {"flag": False},
            "chips_note": sensitivity()["notes"]["general_tendency"]}


# --------------------------------------------------------------------------- glossary values

def glossary_values(conn: sqlite3.Connection, code: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    m = macro_payload(conn, code)
    it = m["items"]
    us, jp, sp, fx = it["us10y"], it["jp10y"], it["us_spread"], it["usdjpy"]
    if jp["state"] == "ok":
        out.update(jp10=num(jp["value"], 2), jp_month=_month(jp["as_of"]))
    if us["state"] == "ok":
        out.update(us10=num(us["value"], 2), us_as_of=us["as_of"])
    if sp["state"] == "ok":
        out.update(sp_long=num(sp["legs"]["long"], 2), sp_short=num(sp["legs"]["short"], 2),
                   spread=signed(sp["value"], 2), sp_as_of=sp["as_of"])
    if fx["state"] == "ok" and fx["tankan"]["available"]:
        g = fx["tankan"]
        out.update(usd=num(fx["value"], 2), tankan=num(g["rate"], 2), tankan_label=g["survey_label"],
                   gap=signed(g["gap_yen"], 2), gap_pct=signed(g["gap_pct"], 2), fx_as_of=fx["as_of"])
    if code:
        from .queries import stock_header

        head = stock_header(conn, resolve_code(code))
        if head:
            prof = sector_profile(head["stock"]["sector17"]["code"], head["stock"]["sector17"]["name"])
            if prof["state"] == "ok":
                tags = {t["factor"]: t["effect_label"] for t in prof["tags"]}
                out.update(sector=prof["sector17"]["name"], cycle=prof["cycle"]["label"],
                           rate_up=tags["rate_up"], yen_weak=tags["yen_weak"])
    out["as_of"] = us.get("as_of") or jp.get("as_of") or ""
    if not out["as_of"]:
        out.pop("as_of")
    return out
