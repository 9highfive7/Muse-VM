"""Builders for synthetic /v2/fins/summary rows (J-Quants v2 field names,
values as strings, "" for not disclosed) and market-cap bars. Not real data.

Amounts are given in 億円 (×1e8 yen) to keep the hand-computed expectations
in the tests readable.
"""
from __future__ import annotations

from datetime import date, timedelta

OKU = 100_000_000  # 1億円


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and not v.is_integer():
        return repr(v)
    return str(int(v)) if isinstance(v, (int, float)) else str(v)


def _yen(v):
    return None if v is None else v * OKU


PERIOD_END_MONTHS = {"1Q": 3, "2Q": 6, "3Q": 9}


def _period_end(fy_start: str, period: str, fy_end: str) -> str:
    if period == "FY":
        return fy_end
    s = date.fromisoformat(fy_start)
    m0 = s.month - 1 + PERIOD_END_MONTHS[period]        # 0-based month after the period end
    nxt = date(s.year + m0 // 12, m0 % 12 + 1, 1)
    return (nxt - timedelta(days=1)).isoformat()


def stmt(code: str, no: str, disc_date: str, period: str, fy_start: str, fy_end: str, *,
         std: str = "JP", cons: str = "Consolidated", sales=None, op=None, odp=None, np_=None, eps=None,
         ta=None, eq=None, eqar=None, cfo=None, cfi=None, cff=None, div_ann=None,
         f_sales=None, f_op=None, f_np=None, f_eps=None, f_div=None,
         nx_start=None, nx_end=None, nx_sales=None, nx_op=None, nx_np=None, nx_eps=None, nx_div=None,
         time: str = "15:00:00") -> dict:
    """One financial-statement disclosure. Money args in 億円; eps/div in 円; eqar as ratio (0.523)."""
    return {
        "DiscDate": disc_date, "DiscTime": time, "Code": code, "DiscNo": no,
        "DocType": f"{period}FinancialStatements_{cons}_{std}",
        "CurPerType": period, "CurPerSt": fy_start, "CurPerEn": _period_end(fy_start, period, fy_end),
        "CurFYSt": fy_start, "CurFYEn": fy_end,
        "NxtFYSt": _s(nx_start), "NxtFYEn": _s(nx_end),
        "Sales": _s(_yen(sales)), "OP": _s(_yen(op)), "OdP": _s(_yen(odp)), "NP": _s(_yen(np_)),
        "EPS": _s(eps), "DEPS": "", "TA": _s(_yen(ta)), "Eq": _s(_yen(eq)), "EqAR": _s(eqar), "BPS": "",
        "CFO": _s(_yen(cfo)), "CFI": _s(_yen(cfi)), "CFF": _s(_yen(cff)), "CashEq": "",
        "Div1Q": "", "Div2Q": "", "Div3Q": "", "DivFY": "", "DivAnn": _s(div_ann),
        "FSales": _s(_yen(f_sales)), "FOP": _s(_yen(f_op)), "FOdP": "", "FNP": _s(_yen(f_np)),
        "FEPS": _s(f_eps), "FDivAnn": _s(f_div),
        "NxFSales": _s(_yen(nx_sales)), "NxFOP": _s(_yen(nx_op)), "NxFOdP": "", "NxFNp": _s(_yen(nx_np)),
        "NxFEPS": _s(nx_eps), "NxFDivAnn": _s(nx_div),
    }


def revision(code: str, no: str, disc_date: str, fy_start: str, fy_end: str, *, f_sales=None, f_op=None,
             f_np=None, f_eps=None, f_div=None, dividend_only: bool = False) -> dict:
    """An earnings (or dividend) forecast revision disclosure for the current FY."""
    return {
        "DiscDate": disc_date, "DiscTime": "15:00:00", "Code": code, "DiscNo": no,
        "DocType": "DividendForecastRevision" if dividend_only else "EarnForecastRevision",
        "CurPerType": "FY", "CurPerSt": fy_start, "CurPerEn": fy_end, "CurFYSt": fy_start, "CurFYEn": fy_end,
        "NxtFYSt": "", "NxtFYEn": "",
        "Sales": "", "OP": "", "OdP": "", "NP": "", "EPS": "", "TA": "", "Eq": "", "EqAR": "",
        "CFO": "", "CFI": "", "CFF": "", "DivAnn": "",
        "FSales": _s(_yen(f_sales)), "FOP": _s(_yen(f_op)), "FNP": _s(_yen(f_np)), "FEPS": _s(f_eps),
        "FDivAnn": _s(f_div),
    }


def fy_dates(end_year: int, end_month: int = 3) -> tuple[str, str]:
    """(fy_start, fy_end) of a 12-month fiscal year ending in end_year/end_month."""
    end = date(end_year + (end_month // 12), end_month % 12 + 1, 1) - timedelta(days=1)
    sm = end_month % 12 + 1
    start = date(end_year - 1 if sm != 1 else end_year, sm, 1)
    return start.isoformat(), end.isoformat()


def store(conn, rows: list[dict], fetched_at: str = "2026-11-21T00:00:00+00:00"):
    from stockapp.fins import store_fins_summary

    with conn:
        return store_fins_summary(conn, rows, fetched_at)


def weekdays(start: str, end: str) -> list[str]:
    d, e = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    while d <= e:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def store_bars(conn, code: str, rows: list[tuple]):
    """rows: (date, close_unadjusted, adj_factor, market_cap_in_million_yen)."""
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_bars(code, date, open, high, low, close, adj_factor, adj_open, adj_high,"
            " adj_low, adj_close, volume, adj_volume, market_cap, fetched_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,1000,1000,?,'2026-11-21T00:00:00+00:00')",
            [(code, d, c, c, c, c, f, c, c, c, c, m) for d, c, f, m in rows],
        )


# --------------------------------------------------------------------------- canned companies

def march_company(code: str, *, years=(2022, 2023, 2024, 2025, 2026), current_2q: bool = True) -> list[dict]:
    """A plain 3月期 company: 5 completed fiscal years (1Q–FY each) plus the
    current year's 1Q and 2Q. Each FY statement carries the next year's
    forecast. Quarterly cumulative pattern per year: 25/50/75/100 of sales."""
    rows = []
    n = 0
    for y in years:
        fs, fe = fy_dates(y)
        base = 1000 + (y - years[0]) * 100       # 億円 full-year sales
        opf = base // 10
        nx_s, nx_e = fy_dates(y + 1)
        for k, pt in enumerate(("1Q", "2Q", "3Q", "FY"), 1):
            n += 1
            disc = {"1Q": f"{y - 1}-08-05", "2Q": f"{y - 1}-11-05", "3Q": f"{y}-02-05", "FY": f"{y}-05-10"}[pt]
            extra = {}
            if pt == "FY":
                extra = dict(nx_start=nx_s, nx_end=nx_e, nx_sales=base + 100, nx_op=(base + 100) // 10,
                             nx_np=(base + 100) // 15, nx_eps=100, nx_div=30, div_ann=30, eps=100,
                             cfo=opf * 1.2, cfi=-opf * 0.5, cff=-opf * 0.3)
            else:
                extra = dict(f_sales=base, f_op=opf, f_np=base // 15, f_eps=100, f_div=30)
            rows.append(stmt(code, f"{code}-{n:03d}", disc, pt, fs, fe, sales=base * k / 4, op=opf * k / 4,
                             np_=base // 15 * k / 4, ta=5000 + y, eq=2500, eqar=0.5, **extra))
    if current_2q:
        y = years[-1] + 1
        fs, fe = fy_dates(y)
        base = 1000 + (y - years[0]) * 100
        for k, pt in ((1, "1Q"), (2, "2Q")):
            n += 1
            disc = {"1Q": f"{y - 1}-08-05", "2Q": f"{y - 1}-11-05"}[pt]
            rows.append(stmt(code, f"{code}-{n:03d}", disc, pt, fs, fe, sales=base * k / 4, op=base // 10 * k / 4,
                             np_=base // 15 * k / 4, ta=5600, eq=2600, eqar=0.49, f_sales=base, f_op=base // 10,
                             f_np=base // 15, f_eps=100, f_div=30))
    return rows
