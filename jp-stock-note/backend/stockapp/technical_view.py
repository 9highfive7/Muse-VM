"""「テクニカルで見る」: 一般的なルールによる今の評価と、この銘柄での過去の単純バックテスト。

This block is the one place where the app says 「買いです／売りです」 and shows a
勝率 (explicit user request, an exception to 要件 6.1 limited to this block).
To keep that from being misread, every evaluation carries the indicator's
actual values and the rule, and every win rate carries its test conditions
(sample count, holding period, cost handling, period) plus the
"past results do not guarantee the future" note. All wording comes from
content/technical_view.json via wording.render_tech_view (which still rejects
every banned word other than 「勝率」).

Indicator values are the existing ones (indicators.compute_frame); nothing is
stored, everything is computed per request (要件 4.4, ~1,250 bars at most).

Backtest
--------
* Signal days per indicator (``signal_events``):
    rsi        buy: RSI(14) <= 30, sell: RSI(14) >= 70 (every such day)
    bollinger  buy: close <= -2σ,  sell: close >= +2σ   (every such day)
    macd       buy: MACD crosses above the signal, sell: crosses below
    ma_cross   buy: golden cross (25 over 75),     sell: dead cross
* Entry at the signal day's adjusted close C(i), exit at C(i+H).
  r = C(i+H)/C(i) - 1. Buy wins if r > 0, sell wins if r < 0, r == 0 is a draw
  (not a win). Direction hit rate, not short-sale P&L.
* Non-overlap: per side and horizon, a signal on day j is counted only when
  j >= i + H for the previously counted day i (``non_overlapping``).
* Signals whose exit day is beyond the stored data are "pending" and excluded.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind
from .technicals import Analysis, analyze
from .wording import num, render_tech_view as t, signed, tech_view_templates

INDICATORS = ("rsi", "macd", "ma_cross", "bollinger")
DEFAULT_INDICATOR = "rsi"
HORIZONS = (5, 10, 20)
MAIN_HORIZON = 10
MIN_SAMPLES = 10
SIDES = ("buy", "sell")

RSI_BUY, RSI_SELL = 30.0, 70.0


# --------------------------------------------------------------------------- evaluation (今の評価)

def rsi_side(rsi: float | None) -> str | None:
    """Compared on the value as displayed (1 decimal) so text and verdict agree."""
    if rsi is None or math.isnan(rsi):
        return None
    r = round(float(rsi), 1)
    return "buy" if r <= RSI_BUY else "sell" if r >= RSI_SELL else "neutral"


def bollinger_side(close: float | None, lower: float | None, upper: float | None) -> str | None:
    if any(v is None or math.isnan(v) for v in (close, lower, upper)):
        return None
    return "buy" if close <= lower else "sell" if close >= upper else "neutral"


def cross_side(last_kind: str | None, gap: float | None = None) -> str:
    """Latest cross golden -> buy, dead -> sell, none -> neutral. With ``gap``
    (MACD - signal) the state must still be on the cross's side (プラス圏 /
    マイナス圏); a gap of exactly 0 is neutral."""
    if last_kind == "golden" and (gap is None or gap > 0):
        return "buy"
    if last_kind == "dead" and (gap is None or gap < 0):
        return "sell"
    return "neutral"


def _f(v) -> float | None:
    if v is None:
        return None
    f = float(v)
    return None if math.isnan(f) else f


def evaluate(a: Analysis, indicator: str) -> dict:
    short = t(f"short.{indicator}")
    if a.empty:
        return {"side": "unavailable", "text": t("eval.unavailable", short=short),
                "basis": t(f"basis.{indicator}_unavailable", as_of="—"), "values": {}, "as_of": None}
    f, as_of = a.frame, a.as_of
    last = f.iloc[-1]
    values: dict = {}
    side: str | None
    basis: str

    if indicator == "rsi":
        rsi = _f(last["rsi"])
        side = rsi_side(rsi)
        values = {"rsi": None if rsi is None else round(rsi, 2), "buy_at_or_below": RSI_BUY,
                  "sell_at_or_above": RSI_SELL}
        basis = (t("basis.rsi_unavailable", as_of=as_of) if side is None
                 else t("basis.rsi", rsi=num(rsi), as_of=as_of))

    elif indicator == "bollinger":
        close, lo, mid, up = (_f(last[k]) for k in ("close", "bb_lower", "bb_mid", "bb_upper"))
        side = bollinger_side(close, lo, up)
        values = {k: (None if v is None else round(v, 2)) for k, v in
                  (("close", close), ("bb_upper", up), ("bb_mid", mid), ("bb_lower", lo))}
        basis = (t("basis.bollinger_unavailable", as_of=as_of) if side is None
                 else t("basis.bollinger", close=num(close), upper=num(up), mid=num(mid), lower=num(lo),
                        as_of=as_of))

    elif indicator == "macd":
        m, s = _f(last["macd"]), _f(last["macd_signal"])
        if m is None or s is None:
            side, basis = None, t("basis.macd_unavailable", as_of=as_of)
        else:
            events = ind.crosses(f["macd"], f["macd_signal"])
            gap = m - s
            side = cross_side(events[-1][1] if events else None, gap)
            values = {"macd": round(m, 4), "macd_signal": round(s, 4), "macd_hist": round(gap, 4)}
            if events:
                i, kind = events[-1]
                days = len(f) - 1 - i
                values.update(last_cross={"date": f["date"].iloc[i], "kind": kind, "business_days_ago": days})
                basis = t("basis.macd", macd=num(m, 2), signal=num(s, 2), hist=num(gap, 2),
                          cross_date=f["date"].iloc[i], cross_dir=t("cross.up" if kind == "golden" else "cross.down"),
                          days=days, as_of=as_of)
            else:
                values["last_cross"] = None
                first = f.dropna(subset=["macd_signal"])["date"].iloc[0]
                basis = t("basis.macd_nocross", macd=num(m, 2), signal=num(s, 2), hist=num(gap, 2),
                          **{"from": first, "to": as_of}, as_of=as_of)

    elif indicator == "ma_cross":
        s25, s75 = _f(last["sma25"]), _f(last["sma75"])
        if f["sma75"].notna().sum() < 2 or s25 is None or s75 is None:
            side, basis = None, t("basis.ma_cross_unavailable", as_of=as_of)
        else:
            events = ind.crosses(f["sma25"], f["sma75"])
            side = cross_side(events[-1][1] if events else None)
            values = {"sma25": round(s25, 2), "sma75": round(s75, 2)}
            if events:
                i, kind = events[-1]
                days = len(f) - 1 - i
                values["last_cross"] = {"date": f["date"].iloc[i], "kind": kind, "business_days_ago": days}
                basis = t("basis.ma_cross", cross_date=f["date"].iloc[i],
                          cross_dir=t("cross.up" if kind == "golden" else "cross.down"),
                          cross_name=t(f"cross.{kind}"), days=days, sma25=num(s25), sma75=num(s75), as_of=as_of)
            else:
                values["last_cross"] = None
                basis = t("basis.ma_cross_none", **{"from": f["date"].iloc[0], "to": as_of},
                          sma25=num(s25), sma75=num(s75), as_of=as_of)
    else:  # pragma: no cover - guarded by the API pattern
        raise ValueError(indicator)

    if side is None:
        return {"side": "unavailable", "text": t("eval.unavailable", short=short), "basis": basis,
                "values": values, "as_of": as_of}
    return {"side": side, "text": t("eval.sentence", short=short, verdict=t(f"verdict.{side}")),
            "basis": basis, "values": values, "as_of": as_of}


# --------------------------------------------------------------------------- backtest

def signal_events(frame: pd.DataFrame, indicator: str) -> dict[str, list[int]]:
    """Row positions of buy / sell signal days (ascending)."""
    out: dict[str, list[int]] = {"buy": [], "sell": []}
    if frame.empty:
        return out
    if indicator == "rsi":
        for i, v in enumerate(frame["rsi"].to_numpy(dtype=float)):
            s = rsi_side(v)
            if s in out:
                out[s].append(i)
    elif indicator == "bollinger":
        c = frame["close"].to_numpy(dtype=float)
        lo = frame["bb_lower"].to_numpy(dtype=float)
        up = frame["bb_upper"].to_numpy(dtype=float)
        for i in range(len(c)):
            s = bollinger_side(c[i], lo[i], up[i])
            if s in out:
                out[s].append(i)
    elif indicator in ("macd", "ma_cross"):
        a, b = ("macd", "macd_signal") if indicator == "macd" else ("sma25", "sma75")
        for i, kind in ind.crosses(frame[a], frame[b]):
            out["buy" if kind == "golden" else "sell"].append(i)
    else:
        raise ValueError(indicator)
    return out


def non_overlapping(positions: list[int], horizon: int) -> list[int]:
    """Keep a signal only once the previous kept one's holding period is over
    (next kept j satisfies j >= i + horizon)."""
    kept: list[int] = []
    for p in positions:
        if not kept or p >= kept[-1] + horizon:
            kept.append(p)
    return kept


@dataclass
class SideResult:
    side: str
    horizon: int
    signals: int            # non-overlapping signal days, incl. pending
    samples: int            # completed (exit day within the data)
    pending: int
    wins: int
    losses: int
    draws: int
    win_rate_pct: float | None
    avg_return_pct: float | None
    entries: list[dict]

    @property
    def small_sample(self) -> bool:
        return self.samples < MIN_SAMPLES


def backtest_side(close: np.ndarray, dates: list[str], positions: list[int], side: str, horizon: int) -> SideResult:
    kept = non_overlapping(positions, horizon)
    wins = losses = draws = 0
    rets: list[float] = []
    entries: list[dict] = []
    pending = 0
    for i in kept:
        j = i + horizon
        if j >= len(close):
            pending += 1
            continue
        r = close[j] / close[i] - 1.0
        rets.append(r)
        if r == 0:
            draws += 1
            result = "draw"
        elif (r > 0) == (side == "buy"):
            wins += 1
            result = "win"
        else:
            losses += 1
            result = "loss"
        entries.append({"date": dates[i], "exit_date": dates[j], "entry_close": round(float(close[i]), 4),
                        "exit_close": round(float(close[j]), 4), "return_pct": round(r * 100, 4), "result": result})
    n = len(rets)
    return SideResult(side, horizon, len(kept), n, pending, wins, losses, draws,
                      None if n == 0 else wins / n * 100.0,
                      None if n == 0 else float(np.mean(rets)) * 100.0, entries)


def baseline(close: np.ndarray, horizon: int) -> dict:
    """Share of all days whose price was higher ``horizon`` days later."""
    if len(close) <= horizon:
        return {"horizon": horizon, "days": 0, "up_days": 0, "up_pct": None}
    r = close[horizon:] / close[:-horizon] - 1.0
    ups = int((r > 0).sum())
    return {"horizon": horizon, "days": int(len(r)), "up_days": ups, "up_pct": ups / len(r) * 100.0}


def period_text(first: str, last: str, bars: int) -> str:
    d0, d1 = pd.Timestamp(first), pd.Timestamp(last)
    days = (d1 - d0).days
    years = days / 365.25
    if years >= 1:
        span = t("span.years", years=f"{round(years, 1):g}")
    else:
        span = t("span.months", months=max(1, round(days / 30.44)))
    return t("period", **{"from": first, "to": last}, span=span, bars=f"{bars:,}")


def _pct(v: float | None) -> int | None:
    return None if v is None else int(round(v))


def _result_json(r: SideResult) -> dict:
    return {
        "side": r.side, "side_label": t(f"side.{r.side}"), "horizon": r.horizon,
        "signals": r.signals, "samples": r.samples, "pending": r.pending,
        "wins": r.wins, "losses": r.losses, "draws": r.draws,
        "win_rate_pct": None if r.win_rate_pct is None else round(r.win_rate_pct, 2),
        "win_rate_display": _pct(r.win_rate_pct),
        "avg_return_pct": None if r.avg_return_pct is None else round(r.avg_return_pct, 4),
        "avg_return_display": None if r.avg_return_pct is None else signed(r.avg_return_pct),
        "small_sample": r.small_sample,
        "note": " ".join(x for x in (
            t("cell.none") if r.samples == 0 else (t("small_sample") if r.small_sample else ""),
            t("cell.pending", pending=r.pending) if r.pending else "") if x),
        "entries": r.entries,
    }


def headline(side: str, results: dict[str, list[SideResult]], period: str) -> dict:
    if side == "unavailable":
        return {"kind": "unavailable", "text": t("headline.unavailable"), "detail": None, "win_rate_pct": None,
                "label": None, "small_sample": None, "small_sample_text": None}
    if side == "neutral":
        return {"kind": "neutral", "text": t("headline.neutral", period=period), "detail": None,
                "win_rate_pct": None, "label": None, "small_sample": None, "small_sample_text": None}
    r = next(x for x in results[side] if x.horizon == MAIN_HORIZON)
    label = t("label.win_rate_main", side=t(f"side.{side}"), horizon=MAIN_HORIZON)
    if r.samples == 0:
        return {"kind": "no_samples", "label": label, "win_rate_pct": None, "detail": None,
                "text": t("headline.none", horizon=MAIN_HORIZON, side=t(f"side.{side}"), period=period),
                "small_sample": True, "small_sample_text": t("small_sample")}
    return {
        "kind": "rate", "label": label,
        "win_rate_pct": _pct(r.win_rate_pct),
        "text": t("headline.rate", win_rate=_pct(r.win_rate_pct)),
        "detail": t("headline.detail", side=t(f"side.{side}"), n=r.samples, wins=r.wins, losses=r.losses,
                    draws=t("headline.draws", draws=r.draws) if r.draws else "", horizon=MAIN_HORIZON,
                    avg=signed(r.avg_return_pct), period=period),
        "small_sample": r.small_sample,
        "small_sample_text": t("small_sample") if r.small_sample else None,
    }


def labels() -> dict[str, str]:
    """Static block labels (no placeholders) for the frontend."""
    return {k.removeprefix("label."): v for k, v in tech_view_templates().items()
            if k.startswith("label.") and "{" not in v}


def backtest_payload(conn: sqlite3.Connection, code: str, indicator: str = DEFAULT_INDICATOR) -> dict:
    if indicator not in INDICATORS:
        raise ValueError(indicator)
    a = analyze(conn, code)
    ev = evaluate(a, indicator)
    out = {
        "code": a.code,
        "state": "no_data" if a.empty else "ok",
        "as_of": a.as_of,
        "indicator": indicator,
        "indicators": [{"key": k, "label": t(f"indicator.{k}")} for k in INDICATORS],
        "labels": labels(),
        "evaluation": ev,
        "notes": [t("note.future"), t("note.mechanical"), t("note.indicators")],
    }
    if a.empty:
        out["backtest"] = None
        out["headline"] = headline("unavailable", {}, "")
        return out

    f = a.frame
    close = f["close"].to_numpy(dtype=float)
    dates = f["date"].tolist()
    period = period_text(dates[0], dates[-1], len(dates))
    events = signal_events(f, indicator)
    results = {s: [backtest_side(close, dates, events[s], s, h) for h in HORIZONS] for s in SIDES}
    base = [baseline(close, h) for h in HORIZONS]
    main_base = next(b for b in base if b["horizon"] == MAIN_HORIZON)
    conditions = [
        t("cond.entry", horizons="・".join(str(h) for h in HORIZONS), main=MAIN_HORIZON),
        t("cond.win"),
        t("cond.overlap", horizon=MAIN_HORIZON),
        t("cond.small", min=MIN_SAMPLES),
        t("cond.cost"),
    ]
    if main_base["up_pct"] is not None:
        conditions.append(t("cond.baseline", horizon=MAIN_HORIZON, up=_pct(main_base["up_pct"]),
                            n=f"{main_base['days']:,}", ups=f"{main_base['up_days']:,}"))
    out["headline"] = headline(ev["side"], results, period)
    out["backtest"] = {
        "period": {"from": dates[0], "to": dates[-1], "bars": len(dates), "text": period,
                   "price": "adjusted_close"},
        "horizons": list(HORIZONS),
        "main_horizon": MAIN_HORIZON,
        "min_samples": MIN_SAMPLES,
        "signal_rule": t(f"signal.{indicator}"),
        "raw_signal_days": {s: len(events[s]) for s in SIDES},
        "results": {s: [_result_json(r) for r in results[s]] for s in SIDES},
        "baseline": [{**b, "up_pct": None if b["up_pct"] is None else round(b["up_pct"], 2)} for b in base],
        "costs_included": False,
        "conditions": conditions,
        "table_caption": t("label.table_caption", period=period),
    }
    return out
