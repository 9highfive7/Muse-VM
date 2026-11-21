"""Phase 1 read-side payloads: chart, 4-stage technicals, price-position card,
glossary substitution. DB only (never calls J-Quants); indicators are computed
on request (要件 4.4)."""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from . import indicators as ind
from .queries import resolve_code
from .wording import fin_glossary, glossary, macro_glossary, num, placeholders, render, signed

RANGES = {"3m": 3, "6m": 6, "1y": 12, "3y": 36, "5y": 60}
DEFAULT_RANGE = "1y"

MA_STYLES = [
    {"key": "sma5", "label": "5日", "line": "thin_solid"},
    {"key": "sma25", "label": "25日", "line": "thick_solid"},
    {"key": "sma75", "label": "75日", "line": "dashed"},
    {"key": "sma200", "label": "200日", "line": "dotted"},
]


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


def _list(series, digits: int = 4) -> list[float | None]:
    return [_r(v, digits) for v in series]


@dataclass
class Analysis:
    code: str
    bars: pd.DataFrame
    frame: pd.DataFrame
    topix: pd.DataFrame
    aligned: pd.DataFrame

    @property
    def empty(self) -> bool:
        return self.frame.empty

    @property
    def as_of(self) -> str | None:
        return None if self.frame.empty else str(self.frame["date"].iloc[-1])

    def last(self, col: str) -> float | None:
        if self.frame.empty:
            return None
        return _r(self.frame[col].iloc[-1], 10)


def analyze(conn: sqlite3.Connection, code: str) -> Analysis:
    code5 = resolve_code(code)
    bars = ind.load_bars(conn, code5)
    frame = ind.compute_frame(bars)
    topix = ind.load_topix(conn)
    aligned = ind.align_with_topix(bars, topix) if not bars.empty else pd.DataFrame(
        columns=["date", "close", "volume", "topix"])
    return Analysis(code5, bars, frame, topix, aligned)


def range_slice(a: Analysis, rng: str) -> pd.DataFrame:
    if a.empty:
        return a.frame
    months = RANGES.get(rng, RANGES[DEFAULT_RANGE])
    last = pd.Timestamp(a.frame["date"].iloc[-1])
    start = (last - pd.DateOffset(months=months)).date().isoformat()
    return a.frame[a.frame["date"] > start]


# --------------------------------------------------------------------------- chart

def _earnings_markers(conn: sqlite3.Connection, code: str, dates: list[str]) -> list[dict]:
    if not dates:
        return []
    rows = conn.execute(
        "SELECT disclosed_date, disclosure_no, period_type, doc_type FROM fins_summary"
        " WHERE code = ? AND disclosed_date IS NOT NULL ORDER BY disclosed_date, disclosure_no",
        (code,),
    ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        d = r["disclosed_date"]
        if d < dates[0] or d > dates[-1]:
            continue
        # place on the first bar on/after the disclosure date
        pos = int(np.searchsorted(np.array(dates), d))
        bar_date = dates[pos] if pos < len(dates) else None
        if bar_date is None:
            continue
        m = out.setdefault(d, {"date": d, "bar_date": bar_date, "label": "決", "disclosures": []})
        m["disclosures"].append({"disclosure_no": r["disclosure_no"], "period_type": r["period_type"],
                                 "doc_type": r["doc_type"]})
    return list(out.values())


def chart_payload(conn: sqlite3.Connection, code: str, rng: str = DEFAULT_RANGE) -> dict:
    a = analyze(conn, code)
    if rng not in RANGES:
        rng = DEFAULT_RANGE
    if a.empty:
        return {"code": a.code, "range": rng, "state": "no_data", "as_of": None, "dates": [], "candles": [],
                "ma": {m["key"]: [] for m in MA_STYLES}, "ma_styles": MA_STYLES,
                "markers": {"earnings": [], "stop_high": [], "stop_low": []},
                "notes": [render("chart.note_adjusted"), render("chart.note_stop")]}
    view = range_slice(a, rng)
    dates = view["date"].tolist()
    candles = [
        {"date": r.date, "open": _r(r.open), "high": _r(r.high), "low": _r(r.low), "close": _r(r.close),
         "volume": _r(r.volume, 2), "upper_limit": bool(r.upper_limit), "lower_limit": bool(r.lower_limit)}
        for r in view.itertuples(index=False)
    ]
    earnings = _earnings_markers(conn, a.code, dates)
    has_fins = conn.execute("SELECT 1 FROM fins_summary WHERE code = ? LIMIT 1", (a.code,)).fetchone()
    notes = [render("chart.note_adjusted"), render("chart.note_stop"),
             render("chart.note_earnings" if has_fins else "chart.note_earnings_none")]
    return {
        "code": a.code,
        "range": rng,
        "state": "ok",
        "as_of": a.as_of,
        "range_start": dates[0],
        "range_end": dates[-1],
        "history_start": str(a.frame["date"].iloc[0]),
        "dates": dates,
        "candles": candles,
        "ma": {m["key"]: _list(view[m["key"]]) for m in MA_STYLES},
        "ma_styles": MA_STYLES,
        "markers": {
            "earnings": earnings,
            "stop_high": view.loc[view["upper_limit"], "date"].tolist(),
            "stop_low": view.loc[view["lower_limit"], "date"].tolist(),
        },
        "notes": notes,
    }


# --------------------------------------------------------------------------- facts

def _fact(key: str, template: str, available: bool, as_of: str | None, **values) -> dict:
    return {"key": key, "text": render(template, as_of=as_of, **values), "as_of": as_of, "available": available}


def _dir(v: float, digits: int = 1) -> str:
    r = round(v, digits)
    return "dir.above" if r > 0 else ("dir.below" if r < 0 else "dir.equal")


def _order_fact(a: Analysis, prefix: str) -> dict:
    vals = {n: a.last(f"sma{n}") for n in ind.SMA_WINDOWS}
    o = ind.ma_order(vals)
    if o["order"] is None:
        return _fact("ma_order", f"{prefix}.order_unavailable", False, a.as_of,
                     missing="・".join(str(n) for n in o["missing"]))
    return _fact("ma_order", f"{prefix}.order", True, a.as_of,
                 order="・".join(f"{n}日" for n in o["order"]), kind=render(f"order.{o['kind']}"))


def latest_values(a: Analysis) -> dict:
    keys = ["close", "sma5", "sma25", "sma75", "sma200", "dev5", "dev25", "dev75", "dev200",
            "rsi", "avg_u", "avg_d", "bb_mid", "bb_sd", "bb_upper", "bb_lower", "pct_b",
            "ema12", "ema26", "macd", "macd_signal", "macd_hist",
            "h52", "l52", "from_high_pct", "from_low_pct", "range_pos"]
    return {k: (None if a.empty else _r(a.frame[k].iloc[-1], 6)) for k in keys}


def stage1(a: Analysis) -> dict:
    v = latest_values(a)
    facts = [_order_fact(a, "s1")]
    if v["sma5"] is not None:
        items = "、".join(
            render("s1.sma_item", n=n, value=num(v[f"sma{n}"])) if v[f"sma{n}"] is not None
            else render("s1.sma_item_unavailable", n=n) for n in ind.SMA_WINDOWS)
        facts.append(_fact("sma_values", "s1.sma_values", True, a.as_of, items=items))
    events = ind.cross_history(a.frame) if not a.empty else []
    latest_cross = events[-1] if events else None
    if latest_cross:
        facts.append(_fact("latest_cross", f"s1.cross_{latest_cross['kind']}", True, a.as_of,
                           date=latest_cross["date"], days=latest_cross["business_days_ago"]))
    elif a.empty or a.frame["sma75"].notna().sum() < 2:
        facts.append(_fact("latest_cross", "s1.cross_unavailable", False, a.as_of))
    else:
        facts.append(_fact("latest_cross", "s1.cross_none", True, a.as_of,
                           **{"from": a.frame["date"].iloc[0], "to": a.as_of}))
    return {
        "title": "①トレンド",
        "facts": facts,
        "notes": [render("s1.note")],
        "latest_cross": None if latest_cross is None else {
            "kind": latest_cross["kind"], "date": latest_cross["date"],
            "business_days_ago": latest_cross["business_days_ago"]},
        "values": {k: v[k] for k in ("sma5", "sma25", "sma75", "sma200", "dev5", "dev25", "dev75", "dev200")},
    }


def stage2(a: Analysis) -> dict:
    v = latest_values(a)
    facts = []
    if v["rsi"] is None:
        facts.append(_fact("rsi", "s2.rsi_unavailable", False, a.as_of))
    else:
        r1 = round(v["rsi"], 1)
        zone = "zone.above70" if r1 > 70 else ("zone.below30" if r1 < 30 else "zone.inside")
        facts.append(_fact("rsi", "s2.rsi", True, a.as_of, rsi=num(v["rsi"]), zone=render(zone)))
    if v["bb_upper"] is None:
        facts.append(_fact("bollinger", "s2.bb_unavailable", False, a.as_of))
    else:
        c = v["close"]
        key = "s2.bb_upper_out" if c > v["bb_upper"] else ("s2.bb_lower_out" if c < v["bb_lower"] else "s2.bb_inside")
        facts.append(_fact("bollinger_position", key, True, a.as_of, pct_b=num(v["pct_b"], 2)))
        facts.append(_fact("bollinger_values", "s2.bb_values", True, a.as_of,
                           bb_mid=num(v["bb_mid"]), bb_upper=num(v["bb_upper"]), bb_lower=num(v["bb_lower"])))
    return {"title": "②過熱感", "facts": facts, "notes": [render("s2.note_rsi"), render("s2.note_bb")],
            "values": {k: v[k] for k in ("rsi", "bb_mid", "bb_upper", "bb_lower", "pct_b")}}


def stage3(a: Analysis) -> dict:
    v = latest_values(a)
    facts = []
    if v["macd_signal"] is None:
        facts.append(_fact("macd", "s3.macd_unavailable", False, a.as_of))
        days = None
    else:
        valid = a.frame.dropna(subset=["macd", "macd_signal"])
        diff = valid["macd"] - valid["macd_signal"]
        if v["macd"] > v["macd_signal"]:
            days = ind.streak(diff > 0)
            facts.append(_fact("macd", "s3.macd_above", True, a.as_of, days=days,
                               macd=num(v["macd"], 2), macd_signal=num(v["macd_signal"], 2)))
        elif v["macd"] < v["macd_signal"]:
            days = ind.streak(diff < 0)
            facts.append(_fact("macd", "s3.macd_below", True, a.as_of, days=days,
                               macd=num(v["macd"], 2), macd_signal=num(v["macd_signal"], 2)))
        else:
            days = 0
            facts.append(_fact("macd", "s3.macd_equal", True, a.as_of, macd=num(v["macd"], 2)))
        d = _dir(v["macd"], 2)
        if d == "dir.equal":
            facts.append(_fact("macd_zero", "s3.macd_zero_equal", True, a.as_of, macd_hist=num(v["macd_hist"], 2)))
        else:
            facts.append(_fact("macd_zero", "s3.macd_zero", True, a.as_of, dir=render(d),
                               macd_hist=num(v["macd_hist"], 2)))
    if v["h52"] is None:
        facts.append(_fact("w52", "s3.w52_unavailable", False, a.as_of))
    else:
        facts.append(_fact("w52", "s3.w52", True, a.as_of, from_high=signed(v["from_high_pct"]),
                           from_low=signed(v["from_low_pct"]), h52=num(v["h52"]), l52=num(v["l52"])))
        if v["range_pos"] is not None:
            facts.append(_fact("w52_pos", "s3.w52_pos", True, a.as_of, range_pos=num(v["range_pos"])))
    vals = {k: v[k] for k in ("macd", "macd_signal", "macd_hist", "h52", "l52", "from_high_pct",
                              "from_low_pct", "range_pos")}
    vals["macd_streak_days"] = days
    return {"title": "③転換・支持抵抗", "facts": facts, "notes": [render("s3.note")], "values": vals}


def _excess_rows(a: Analysis) -> list[dict]:
    rows = ind.excess_returns(a.aligned) if len(a.aligned) else [
        {"n": n, "from_date": None, "to_date": None, "stock_pct": None, "topix_pct": None, "excess_pt": None}
        for n in ind.RETURN_PERIODS]
    as_of = rows[0]["to_date"] if rows and rows[0]["to_date"] else a.as_of
    out = []
    for r in rows:
        if r["excess_pt"] is None:
            text = render("s4.excess_unavailable", n=r["n"], need=r["n"] + 1, as_of=as_of)
        else:
            text = render("s4.excess", n=r["n"], stock=signed(r["stock_pct"]), topix=signed(r["topix_pct"]),
                          excess=signed(r["excess_pt"]), as_of=as_of)
        out.append({**{k: (_r(v) if isinstance(v, float) else v) for k, v in r.items()}, "as_of": as_of,
                    "text": text})
    return out


def stage4(a: Analysis, view_dates: list[str]) -> dict:
    facts = []
    idx = ind.indexed_series(view_dates, a.bars, a.topix) if view_dates else {"t0": None, "stock": [], "topix": [], "rs": []}
    aligned_as_of = str(a.aligned["date"].iloc[-1]) if len(a.aligned) else a.as_of
    if idx["t0"] is None:
        facts.append(_fact("indexed", "s4.indexed_unavailable", False, a.as_of))
    else:
        # latest date in the view where both indexed values exist
        last = max(i for i, (s, t) in enumerate(zip(idx["stock"], idx["topix"]))
                   if s is not None and t is not None and not math.isnan(s) and not math.isnan(t))
        d = view_dates[last]
        facts.append(_fact("indexed", "s4.indexed", True, d, t0=idx["t0"], idx_stock=num(idx["stock"][last]),
                           idx_topix=num(idx["topix"][last])))
        facts.append(_fact("rs", "s4.rs", True, d, t0=idx["t0"], rs=num(idx["rs"][last])))
    excess = _excess_rows(a)
    b = ind.beta_correlation(a.aligned) if len(a.aligned) else ind.BetaResult(None, None, 0, None, None)
    if b.beta is None:
        facts.append(_fact("beta", "s4.beta_unavailable", False, aligned_as_of, n=b.observations))
    else:
        facts.append(_fact("beta", "s4.beta", True, aligned_as_of, beta=num(b.beta, 2),
                           correlation=num(b.correlation, 2), n=b.observations,
                           **{"from": b.window_from, "to": b.window_to}))
    topix_available = not a.topix.empty
    return {
        "title": "④相対比較",
        # Phase 2 leftover: without TOPIX the panel must say so instead of
        # rendering empty chart frames.
        "topix_available": topix_available,
        "topix_message": None if topix_available else render("s4.topix_missing"),
        "facts": facts,
        "notes": [render("s4.note_rs"), render("s4.note_price"), render("s4.note_beta")],
        "t0": idx["t0"],
        "excess_returns": excess,
        "beta": {"beta": _r(b.beta), "correlation": _r(b.correlation), "observations": b.observations,
                 "window_from": b.window_from, "window_to": b.window_to, "min_observations": ind.BETA_MIN_OBS,
                 "window": ind.BETA_WINDOW, "as_of": aligned_as_of},
        "_indexed": idx,
    }


def cross_history_payload(a: Analysis) -> dict:
    events = ind.cross_history(a.frame) if not a.empty else []
    counts, texts = {}, []
    for n in ind.CROSS_FORWARD:
        vals = [e[f"ret{n}_pct"] for e in events]
        c = {"up": sum(1 for v in vals if v is not None and v > 0),
             "down": sum(1 for v in vals if v is not None and v < 0),
             "flat": sum(1 for v in vals if v is not None and v == 0),
             "pending": sum(1 for v in vals if v is None),
             "total": len(vals)}
        counts[f"after_{n}d"] = c
        texts.append(render("cross.counts", n=n, **c))
    out_events = []
    for e in events:
        out_events.append({
            "date": e["date"], "kind": e["kind"], "label": "25日線が75日線を上抜け" if e["kind"] == "golden"
            else "25日線が75日線を下抜け", "close": _r(e["close"]),
            "business_days_ago": e["business_days_ago"],
            "after_20d": {"date": e["ret20_date"], "return_pct": _r(e["ret20_pct"])},
            "after_60d": {"date": e["ret60_date"], "return_pct": _r(e["ret60_pct"])},
        })
    period = None
    if not a.empty:
        period = render("cross.period", **{"from": a.frame["date"].iloc[0], "to": a.as_of, "n": len(a.frame)})
    return {
        "events": out_events,
        "counts": counts,
        "count_texts": texts,
        "period": period,
        "notes": [render("cross.note_small"), render("cross.note_def")],
    }


def technicals_payload(conn: sqlite3.Connection, code: str, rng: str = DEFAULT_RANGE) -> dict:
    a = analyze(conn, code)
    if rng not in RANGES:
        rng = DEFAULT_RANGE
    view = range_slice(a, rng)
    dates = view["date"].tolist()
    s4 = stage4(a, dates)
    idx = s4.pop("_indexed")
    series = {
        "close": _list(view["close"]),
        "rsi": _list(view["rsi"]),
        "bb_mid": _list(view["bb_mid"]), "bb_upper": _list(view["bb_upper"]), "bb_lower": _list(view["bb_lower"]),
        "pct_b": _list(view["pct_b"]),
        "macd": _list(view["macd"]), "macd_signal": _list(view["macd_signal"]), "macd_hist": _list(view["macd_hist"]),
        "idx_stock": _list(idx["stock"]), "idx_topix": _list(idx["topix"]), "rs": _list(idx["rs"]),
    } if dates else {k: [] for k in ("close", "rsi", "bb_mid", "bb_upper", "bb_lower", "pct_b", "macd",
                                    "macd_signal", "macd_hist", "idx_stock", "idx_topix", "rs")}
    return {
        "code": a.code,
        "state": "no_data" if a.empty else "ok",
        "as_of": a.as_of,
        "range": rng,
        "dates": dates,
        "series": series,
        "stage1": stage1(a),
        "stage2": stage2(a),
        "stage3": stage3(a),
        "stage4": s4,
        "cross_history": cross_history_payload(a),
        "values": latest_values(a),
    }


# --------------------------------------------------------------------------- price-position card

def price_position_card(conn: sqlite3.Connection, code: str) -> dict:
    a = analyze(conn, code)
    if a.empty:
        return {"code": a.code, "state": "no_data", "as_of": None, "sentences": []}
    v = latest_values(a)
    s = []
    if v["dev25"] is None:
        s.append(_fact("deviation", "card.dev_unavailable", False, a.as_of))
    else:
        others = {f"dev{n}": signed(v[f"dev{n}"]) if v[f"dev{n}"] is not None else "算出不可" for n in (5, 75, 200)}
        d = _dir(v["dev25"])
        if d == "dir.equal":
            s.append(_fact("deviation", "card.dev_equal", True, a.as_of, **others))
        else:
            s.append(_fact("deviation", "card.dev", True, a.as_of, dev25=signed(v["dev25"]), dir25=render(d),
                           **others))
    s.append(_order_fact(a, "card"))
    if v["range_pos"] is None:
        s.append(_fact("w52", "card.w52_unavailable", False, a.as_of))
    else:
        s.append(_fact("w52", "card.w52", True, a.as_of, from_top=num(100 - v["range_pos"]),
                       from_high=signed(v["from_high_pct"])))
    ex60 = next((r for r in _excess_rows(a) if r["n"] == 60), None)
    if ex60 is None or ex60["excess_pt"] is None:
        s.append(_fact("excess60", "card.excess_unavailable", False, a.as_of))
    else:
        s.append({"key": "excess60", "text": render("card.excess", stock=signed(ex60["stock_pct"]),
                                                     topix=signed(ex60["topix_pct"]),
                                                     excess=signed(ex60["excess_pt"]), as_of=ex60["as_of"]),
                  "as_of": ex60["as_of"], "available": True})
    targets = {"deviation": "stage1", "ma_order": "stage1", "w52": "stage3", "excess60": "stage4"}
    for item in s:
        item["target"] = targets[item["key"]]
    return {"code": a.code, "state": "ok", "as_of": a.as_of, "title": "価格位置", "sentences": s[:4]}


# --------------------------------------------------------------------------- glossary

def glossary_values(a: Analysis) -> dict[str, str]:
    """Formatted values for glossary formula placeholders. Missing -> absent."""
    out: dict[str, str] = {}
    if a.empty:
        return out
    v = latest_values(a)
    out["as_of"] = a.as_of

    def put(key, value, fmt=num, digits=1):
        if value is not None and not (isinstance(value, float) and math.isnan(value)):
            out[key] = fmt(value, digits)

    for k in ("close", "sma25", "bb_mid", "bb_sd", "bb_upper", "bb_lower", "h52", "l52"):
        put(k, v[k])
    put("dev25", v["dev25"], signed)
    put("rsi", v["rsi"])
    put("avg_u", v["avg_u"], num, 2)
    put("avg_d", v["avg_d"], num, 2)
    put("pct_b", v["pct_b"], num, 2)
    for k in ("ema12", "ema26", "macd", "macd_signal", "macd_hist"):
        put(k, v[k], num, 2)
    put("from_high", v["from_high_pct"], signed)
    put("range_pos", v["range_pos"])
    if v["sma25"] is not None:
        put("sum25", float(a.frame["close"].iloc[-25:].sum()))

    events = ind.cross_history(a.frame)
    for kind, prefix in (("golden", "gc"), ("dead", "dc")):
        ev = [e for e in events if e["kind"] == kind]
        if ev:
            i = int(a.frame.index[a.frame["date"] == ev[-1]["date"]][0])
            out[f"{prefix}_date"] = ev[-1]["date"]
            put(f"{prefix}_prev_s", float(a.frame["sma25"].iloc[i - 1]))
            put(f"{prefix}_prev_l", float(a.frame["sma75"].iloc[i - 1]))
            put(f"{prefix}_s", float(a.frame["sma25"].iloc[i]))
            put(f"{prefix}_l", float(a.frame["sma75"].iloc[i]))

    b = ind.beta_correlation(a.aligned) if len(a.aligned) else None
    if b is not None and b.beta is not None:
        out.update(beta=num(b.beta, 2), correlation=num(b.correlation, 2), beta_n=str(b.observations),
                   beta_from=b.window_from, beta_to=b.window_to)
        out["cov"] = f"{b.cov:.3e}".replace("-", "−")
        out["var_topix"] = f"{b.var_topix:.3e}".replace("-", "−")
        out["sd_stock"] = f"{b.sd_stock:.4f}"
        out["sd_topix"] = f"{b.sd_topix:.4f}"

    view_dates = range_slice(a, DEFAULT_RANGE)["date"].tolist()
    idx = ind.indexed_series(view_dates, a.bars, a.topix) if view_dates else {"t0": None}
    if idx["t0"] is not None:
        pairs = [(i, s, t, r) for i, (s, t, r) in enumerate(zip(idx["stock"], idx["topix"], idx["rs"]))
                 if not (pd.isna(s) or pd.isna(t))]
        if pairs:
            i, s, t, r = pairs[-1]
            out.update(t0=idx["t0"], idx_stock=num(s), idx_topix=num(t), rs=num(r))

    ex60 = next((r for r in _excess_rows(a) if r["n"] == 60), None)
    if ex60 and ex60["excess_pt"] is not None:
        out.update(ex60_stock=signed(ex60["stock_pct"]), ex60_topix=signed(ex60["topix_pct"]),
                   ex60_excess=signed(ex60["excess_pt"]), ex60_from=ex60["from_date"], ex60_to=ex60["to_date"])

    first = a.bars.iloc[0]
    if first["raw_close"] and not pd.isna(first["raw_close"]):
        out.update(adj_date=first["date"], adj_close_then=num(first["close"]),
                   raw_close_then=num(first["raw_close"]),
                   adj_ratio=f"{first['close'] / first['raw_close']:.4f}")

    out.update(stop_from=str(a.bars["date"].iloc[0]), stop_to=a.as_of,
               stop_up_n=str(int(a.bars["upper_limit"].sum())), stop_down_n=str(int(a.bars["lower_limit"].sum())))
    return out


def glossary_entry(conn: sqlite3.Connection, term: str, code: str | None = None) -> dict | None:
    """Technical terms (glossary.json, values from the daily bars) and, since
    Phase 2, financial terms (glossary_financial.json, values from the stored
    financial summaries and market caps)."""
    financial = term in fin_glossary()
    macro_term = term in macro_glossary()
    entry = (fin_glossary().get(term) if financial else macro_glossary().get(term) if macro_term
             else glossary().get(term))
    if entry is None:
        return None
    values: dict[str, str] = {}
    a = None
    if macro_term:
        # Phase 3: market-wide values fill in without a code; sector values need one.
        from .macro import glossary_values as macro_values

        values = macro_values(conn, code)
    elif code:
        if financial:
            from .financials import glossary_values as fin_values

            values = fin_values(conn, resolve_code(code))
        else:
            a = analyze(conn, code)
            values = glossary_values(a)
    needed = placeholders(entry["formula"])
    have_all = (bool(code) or macro_term) and needed <= set(values)
    formula = entry["formula"].format(**values) if have_all else entry["formula_generic"]
    reason = None
    if not have_all:
        if macro_term:
            reason = ("必要な金利・為替の系列（FRED）が未取得、または業種の値には銘柄の指定が必要なため、"
                      "一般式を表示しています。")
        elif not code:
            reason = "銘柄が指定されていないため、一般式を表示しています。"
        elif financial:
            reason = ("この銘柄の決算サマリーが未取得、または必要な値が開示されていない・算出できないため、"
                      "一般式を表示しています。")
        elif a is not None and a.empty:
            reason = "この銘柄の日足が未取得のため、一般式を表示しています。"
        else:
            reason = "この銘柄では必要な値が算出できない（データ不足、または該当する事例がない）ため、一般式を表示しています。"
    return {
        "term": term,
        "label": entry["label"],
        "definition": entry["definition"],
        "formula": formula,
        "formula_is_generic": not have_all,
        "formula_note": reason,
        "misconception": entry["misconception"],
        "code": resolve_code(code) if code else None,
        "as_of": values.get("as_of") or values.get("val_as_of") or values.get("prog_as_of"),
    }
