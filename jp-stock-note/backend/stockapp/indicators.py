"""Indicator calculations (要件 5.1・5.2), pandas.

Conventions (要件 5 共通の前提):
* Prices are the adjusted close C(t); t is the business-day sequence of the
  stored bars (rows with a null adjusted close are dropped up front).
* Where fewer observations exist than a formula needs, the value is NaN
  (-> null in the API). Nothing is filled with 0 or a partial average.
* Zero-volume days stay in price series; return-based statistics (beta,
  correlation) exclude them.

Derived values are never stored (要件 4.4): everything is computed on request.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

import numpy as np
import pandas as pd

SMA_WINDOWS = (5, 25, 75, 200)
RSI_PERIOD = 14
BB_PERIOD, BB_K = 20, 2.0
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
WEEK52_LOOKBACK_DAYS = 364
# The 52-week window counts as covered when stored history begins no later
# than this many calendar days after the window start (a weekend + holiday,
# or the 12/31-1/3 market closure, can sit at the window start).
WEEK52_COVERAGE_SLACK_DAYS = 4
RETURN_PERIODS = (20, 60, 120, 250)
BETA_WINDOW, BETA_MIN_OBS = 250, 200
CROSS_FORWARD = (20, 60)


# --------------------------------------------------------------------------- loading

def load_bars(conn: sqlite3.Connection, code: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT date, adj_open AS open, adj_high AS high, adj_low AS low, adj_close AS close,"
        " adj_volume AS volume, close AS raw_close, adj_factor, upper_limit, lower_limit"
        " FROM daily_bars WHERE code = ? AND adj_close IS NOT NULL ORDER BY date",
        conn, params=(code,),
    )
    df["upper_limit"] = df["upper_limit"].astype(str) == "1"
    df["lower_limit"] = df["lower_limit"].astype(str) == "1"
    return df.reset_index(drop=True)


def load_topix(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT date, close FROM topix_bars WHERE close IS NOT NULL ORDER BY date", conn
    ).reset_index(drop=True)


# --------------------------------------------------------------------------- 5.1 trend

def sma(close: pd.Series, n: int) -> pd.Series:
    """SMA_n(t) = (1/n) Σ_{i=0}^{n-1} C(t-i); NaN until n values exist."""
    return close.rolling(n, min_periods=n).mean()


def deviation(close: pd.Series, ma: pd.Series) -> pd.Series:
    """(C - SMA) / SMA × 100."""
    return (close - ma) / ma * 100.0


def ma_order(values: dict[int, float | None]) -> dict:
    """配列: the 4 SMAs sorted high -> low. Short-first = 短期が上の配列,
    long-first = 長期が上の配列, otherwise 混在. None if any SMA is missing."""
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in values.values()):
        missing = [n for n, v in values.items() if v is None or (isinstance(v, float) and math.isnan(v))]
        return {"order": None, "kind": None, "missing": missing}
    order = sorted(values, key=lambda n: (-values[n], n))
    if order == sorted(values):
        kind = "short_on_top"
    elif order == sorted(values, reverse=True):
        kind = "long_on_top"
    else:
        kind = "mixed"
    return {"order": order, "kind": kind, "missing": []}


def crosses(short: pd.Series, long: pd.Series) -> list[tuple[int, str]]:
    """Golden: S(t-1) <= L(t-1) and S(t) > L(t). Dead: S(t-1) >= L(t-1) and S(t) < L(t).
    Both days must have both averages."""
    s, lg = short.to_numpy(dtype=float), long.to_numpy(dtype=float)
    out: list[tuple[int, str]] = []
    for t in range(1, len(s)):
        a0, b0, a1, b1 = s[t - 1], lg[t - 1], s[t], lg[t]
        if np.isnan(a0) or np.isnan(b0) or np.isnan(a1) or np.isnan(b1):
            continue
        if a0 <= b0 and a1 > b1:
            out.append((t, "golden"))
        elif a0 >= b0 and a1 < b1:
            out.append((t, "dead"))
    return out


# --------------------------------------------------------------------------- 5.2 oscillators

def rsi_wilder(close: pd.Series, n: int = RSI_PERIOD) -> pd.DataFrame:
    """U(t)=max(C(t)-C(t-1),0), D(t)=max(C(t-1)-C(t),0). AvgU/AvgD start as the
    simple mean of the first n values, then AvgU(t)=(AvgU(t-1)×(n-1)+U(t))/n.
    RSI = 100 - 100/(1+AvgU/AvgD); 100 when AvgD = 0."""
    c = close.to_numpy(dtype=float)
    size = len(c)
    avg_u = np.full(size, np.nan)
    avg_d = np.full(size, np.nan)
    rsi = np.full(size, np.nan)
    if size > n:
        diff = np.diff(c)                      # diff[k] = C(k+1) - C(k)
        up = np.maximum(diff, 0.0)
        down = np.maximum(-diff, 0.0)
        au, ad = up[:n].mean(), down[:n].mean()
        avg_u[n], avg_d[n] = au, ad
        for t in range(n + 1, size):
            au = (au * (n - 1) + up[t - 1]) / n
            ad = (ad * (n - 1) + down[t - 1]) / n
            avg_u[t], avg_d[t] = au, ad
        valid = ~np.isnan(avg_u)
        with np.errstate(divide="ignore", invalid="ignore"):
            rsi[valid] = np.where(avg_d[valid] == 0, 100.0, 100.0 - 100.0 / (1.0 + avg_u[valid] / avg_d[valid]))
    idx = close.index
    return pd.DataFrame({"rsi": rsi, "avg_u": avg_u, "avg_d": avg_d}, index=idx)


def bollinger(close: pd.Series, n: int = BB_PERIOD, k: float = BB_K) -> pd.DataFrame:
    """Middle = SMA_20; σ = population std of the last 20 closes (ddof=0);
    bands = middle ± 2σ; %B = (C - lower) / (upper - lower)."""
    mid = close.rolling(n, min_periods=n).mean()
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    width = upper - lower
    pct_b = (close - lower) / width.where(width > 0)
    return pd.DataFrame({"bb_mid": mid, "bb_sd": sd, "bb_upper": upper, "bb_lower": lower, "pct_b": pct_b})


def ema(series: pd.Series, n: int) -> pd.Series:
    """α = 2/(n+1); EMA(t) = α×X(t) + (1-α)×EMA(t-1); seeded with the SMA of the
    first n available values (leading NaN skipped)."""
    x = series.to_numpy(dtype=float)
    out = np.full(len(x), np.nan)
    finite = np.flatnonzero(~np.isnan(x))
    if len(finite) >= n:
        start = finite[0]
        seed_end = start + n - 1
        if not np.isnan(x[start:seed_end + 1]).any():
            alpha = 2.0 / (n + 1)
            value = x[start:seed_end + 1].mean()
            out[seed_end] = value
            for t in range(seed_end + 1, len(x)):
                if np.isnan(x[t]):
                    break  # never bridge a gap
                value = alpha * x[t] + (1 - alpha) * value
                out[t] = value
    return pd.Series(out, index=series.index)


def macd(close: pd.Series) -> pd.DataFrame:
    """MACD = EMA12 - EMA26; signal = 9-period EMA of MACD; histogram = MACD - signal."""
    e12, e26 = ema(close, MACD_FAST), ema(close, MACD_SLOW)
    line = e12 - e26
    signal = ema(line, MACD_SIGNAL)
    return pd.DataFrame({"ema12": e12, "ema26": e26, "macd": line, "macd_signal": signal, "macd_hist": line - signal})


def week52(df: pd.DataFrame) -> pd.DataFrame:
    """H52 = max adjusted high, L52 = min adjusted low over [t-364 days, t].
    Distance from high = C/H52 - 1; range position = (C-L52)/(H52-L52)×100.
    NaN while stored history does not cover the whole window."""
    if df.empty:
        return pd.DataFrame({"h52": [], "l52": [], "from_high_pct": [], "from_low_pct": [], "range_pos": []})
    dt = pd.to_datetime(df["date"])
    hi = pd.Series(df["high"].to_numpy(dtype=float), index=dt)
    lo = pd.Series(df["low"].to_numpy(dtype=float), index=dt)
    window = f"{WEEK52_LOOKBACK_DAYS + 1}D"     # (t-365d, t] == [t-364d, t]
    h52 = hi.rolling(window, min_periods=1).max().to_numpy()
    l52 = lo.rolling(window, min_periods=1).min().to_numpy()
    window_start = dt - pd.Timedelta(days=WEEK52_LOOKBACK_DAYS)
    covered = (dt.iloc[0] - window_start).dt.days.to_numpy() <= WEEK52_COVERAGE_SLACK_DAYS
    h52 = np.where(covered, h52, np.nan)
    l52 = np.where(covered, l52, np.nan)
    c = df["close"].to_numpy(dtype=float)
    span = h52 - l52
    with np.errstate(divide="ignore", invalid="ignore"):
        from_high = (c / h52 - 1.0) * 100.0
        from_low = (c / l52 - 1.0) * 100.0
        pos = np.where(span > 0, (c - l52) / span * 100.0, np.nan)
    return pd.DataFrame({"h52": h52, "l52": l52, "from_high_pct": from_high, "from_low_pct": from_low,
                         "range_pos": pos}, index=df.index)


def compute_frame(bars: pd.DataFrame) -> pd.DataFrame:
    """All per-day single-stock indicators as columns on the bar frame."""
    df = bars.copy()
    c = df["close"]
    for n in SMA_WINDOWS:
        df[f"sma{n}"] = sma(c, n)
        df[f"dev{n}"] = deviation(c, df[f"sma{n}"])
    df = pd.concat([df, rsi_wilder(c), bollinger(c), macd(c), week52(df)], axis=1)
    return df


# --------------------------------------------------------------------------- relative (vs TOPIX)

def align_with_topix(bars: pd.DataFrame, topix: pd.DataFrame) -> pd.DataFrame:
    """Dates where both the stock and TOPIX have a close."""
    left = bars[["date", "close", "volume"]]
    right = topix[["date", "close"]].rename(columns={"close": "topix"})
    return left.merge(right, on="date", how="inner").reset_index(drop=True)


def period_return(closes: pd.Series, n: int) -> float | None:
    """R_n = C(t)/C(t-n) - 1 at the last row (fraction), None if < n+1 rows."""
    if len(closes) <= n:
        return None
    a, b = float(closes.iloc[-1]), float(closes.iloc[-1 - n])
    if not b or math.isnan(a) or math.isnan(b):
        return None
    return a / b - 1.0


def excess_returns(aligned: pd.DataFrame) -> list[dict]:
    rows = []
    for n in RETURN_PERIODS:
        rs, rt = period_return(aligned["close"], n), period_return(aligned["topix"], n)
        rows.append({
            "n": n,
            "from_date": aligned["date"].iloc[-1 - n] if len(aligned) > n else None,
            "to_date": aligned["date"].iloc[-1] if len(aligned) else None,
            "stock_pct": None if rs is None else rs * 100,
            "topix_pct": None if rt is None else rt * 100,
            "excess_pt": None if rs is None or rt is None else (rs - rt) * 100,
        })
    return rows


@dataclass
class BetaResult:
    beta: float | None
    correlation: float | None
    observations: int
    window_from: str | None
    window_to: str | None
    cov: float | None = None
    var_topix: float | None = None
    sd_stock: float | None = None
    sd_topix: float | None = None


def beta_correlation(aligned: pd.DataFrame, window: int = BETA_WINDOW, min_obs: int = BETA_MIN_OBS) -> BetaResult:
    """r(t) = C(t)/C(t-1) - 1 on the aligned series; last ``window`` business
    days; days with zero stock volume excluded; β = Cov/Var(TOPIX),
    ρ = Cov/(σ_s σ_T). Not computed with fewer than ``min_obs`` valid returns."""
    if len(aligned) < 2:
        return BetaResult(None, None, 0, None, None)
    rs = aligned["close"] / aligned["close"].shift(1) - 1.0
    rt = aligned["topix"] / aligned["topix"].shift(1) - 1.0
    zero_volume = aligned["volume"].fillna(np.nan) == 0
    frame = pd.DataFrame({"date": aligned["date"], "rs": rs, "rt": rt, "zero": zero_volume}).iloc[1:]
    frame = frame.iloc[-window:]
    valid = frame[~frame["zero"] & frame["rs"].notna() & frame["rt"].notna()]
    n = len(valid)
    wf, wt = (frame["date"].iloc[0], frame["date"].iloc[-1]) if len(frame) else (None, None)
    if n < min_obs:
        return BetaResult(None, None, n, wf, wt)
    x, y = valid["rs"].to_numpy(), valid["rt"].to_numpy()
    mx, my = x.mean(), y.mean()
    cov = float(((x - mx) * (y - my)).mean())
    var_t = float(((y - my) ** 2).mean())
    sd_s, sd_t = math.sqrt(float(((x - mx) ** 2).mean())), math.sqrt(var_t)
    beta = cov / var_t if var_t > 0 else None
    corr = cov / (sd_s * sd_t) if sd_s > 0 and sd_t > 0 else None
    return BetaResult(beta, corr, n, wf, wt, cov, var_t, sd_s, sd_t)


def indexed_series(dates: list[str], bars: pd.DataFrame, topix: pd.DataFrame) -> dict:
    """Idx(t) = C(t)/C(t0)×100 with the same t0 (first date of ``dates`` where
    both closes exist) for stock and TOPIX; RS(t) = Idx_stock/Idx_TOPIX×100."""
    sc = bars.set_index("date")["close"].reindex(dates)
    tc = topix.set_index("date")["close"].reindex(dates) if not topix.empty else pd.Series(np.nan, index=dates)
    both = sc.notna() & tc.notna()
    if not both.any():
        nulls = [None] * len(dates)
        return {"t0": None, "stock": nulls, "topix": nulls, "rs": nulls}
    t0 = both.idxmax()
    pos0 = dates.index(t0)
    si = sc / sc[t0] * 100.0
    ti = tc / tc[t0] * 100.0
    rs = si / ti * 100.0
    mask = np.arange(len(dates)) < pos0
    si[mask] = np.nan
    ti[mask] = np.nan
    rs[mask] = np.nan
    return {"t0": t0, "stock": si.tolist(), "topix": ti.tolist(), "rs": rs.tolist()}


def cross_history(frame: pd.DataFrame) -> list[dict]:
    """Every 25/75 cross in stored history with the 20/60-business-day returns
    after it (C(t+n)/C(t) - 1). Not-yet-elapsed horizons are None."""
    c = frame["close"].to_numpy(dtype=float)
    out = []
    for i, kind in crosses(frame["sma25"], frame["sma75"]):
        ev = {"date": frame["date"].iloc[i], "kind": kind, "close": float(c[i]),
              "business_days_ago": len(c) - 1 - i}
        for n in CROSS_FORWARD:
            j = i + n
            ev[f"ret{n}_pct"] = (c[j] / c[i] - 1.0) * 100.0 if j < len(c) else None
            ev[f"ret{n}_date"] = frame["date"].iloc[j] if j < len(c) else None
        out.append(ev)
    return out


def streak(flags: pd.Series) -> int:
    """Number of consecutive True values ending at the last row."""
    count = 0
    for v in reversed(flags.to_list()):
        if v is True or (isinstance(v, (bool, np.bool_)) and bool(v)):
            count += 1
        else:
            break
    return count
