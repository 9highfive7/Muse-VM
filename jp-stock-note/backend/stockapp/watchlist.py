"""Phase 4: ウォッチリスト and the after-close digest (要件 3.9, research-04).

* Adding needs a non-empty watch reason (監視理由); optional preset tags.
* Cap: ``watch_rules.json`` watchlist_max (30, provisional). Beyond -> error.
* Removing sets removed_at (要件 7.3: 履歴を保持), never deletes.
* Order: added order / code / tag only. No sort by price change, no
  rankings, no popularity, no push notification (要件 3.9 / 6.2).
* The digest lists, per watchlist stock, factual changes since the previous
  business day from stored data only: newly stored disclosures, newly
  matched 確認ポイント, and a day move beyond the stock's own recent
  volatility band. If nothing changed it says so.
"""
from __future__ import annotations

import json
import math
import sqlite3
from functools import lru_cache
from typing import Any

from . import financials, fins, queries
from .db import utc_now
from .queries import resolve_code
from .wording import CONTENT_DIR, num, render, signed

RULES_PATH = CONTENT_DIR / "watch_rules.json"
SORTS = ("added", "code", "tag")


class WatchError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@lru_cache(maxsize=1)
def rules() -> dict:
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _active_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM watchlist WHERE removed_at IS NULL ORDER BY added_at, id").fetchall()


def active_codes(conn: sqlite3.Connection) -> list[str]:
    return [r["code"] for r in _active_rows(conn)]


def add(conn: sqlite3.Connection, code: Any, reason: Any, tags: Any = None) -> dict:
    cfg = rules()
    text = reason.strip() if isinstance(reason, str) else ""
    if not text:
        raise WatchError(422, render("watch.reason_required"))
    if len(text) > cfg["reason_max_len"]:
        raise WatchError(422, render("watch.reason_too_long", max=cfg["reason_max_len"]))
    tags = tags or []
    if not isinstance(tags, list) or any(t not in cfg["tags"] for t in tags):
        raise WatchError(422, render("watch.bad_tag"))
    tags = [t for t in cfg["tags"] if t in tags]          # dedupe, preset order
    if not isinstance(code, str) or not code.strip() or len(code) > 12:
        raise WatchError(422, render("watch.not_found"))
    code5 = resolve_code(code)
    if queries.stock_header(conn, code5) is None:
        raise WatchError(404, render("watch.not_found"))
    with conn:
        existing = conn.execute("SELECT added_at FROM watchlist WHERE code=? AND removed_at IS NULL",
                                (code5,)).fetchone()
        if existing:
            raise WatchError(409, render("watch.already", added=existing["added_at"][:10]))
        n = conn.execute("SELECT COUNT(*) FROM watchlist WHERE removed_at IS NULL").fetchone()[0]
        if n >= cfg["watchlist_max"]:
            raise WatchError(409, render("watch.cap_reached", max=cfg["watchlist_max"], n=n))
        conn.execute("INSERT INTO watchlist(code, reason, tags_json, added_at) VALUES (?,?,?,?)",
                     (code5, text, json.dumps(tags, ensure_ascii=False), utc_now()))
    return entry(conn, code5)


def remove(conn: sqlite3.Connection, code: str) -> dict:
    code5 = resolve_code(code)
    with conn:
        cur = conn.execute("UPDATE watchlist SET removed_at=? WHERE code=? AND removed_at IS NULL", (utc_now(), code5))
    if cur.rowcount == 0:
        raise WatchError(404, render("watch.not_listed"))
    return {"code": code5, "removed": True}


def entry(conn: sqlite3.Connection, code: str) -> dict | None:
    """The active row of one code (stock page: 「ウォッチリストに追加済み」)."""
    r = conn.execute("SELECT * FROM watchlist WHERE code=? AND removed_at IS NULL", (resolve_code(code),)).fetchone()
    return None if r is None else _row(r)


def _row(r: sqlite3.Row) -> dict:
    return {"code": r["code"], "reason": r["reason"], "tags": json.loads(r["tags_json"]), "added_at": r["added_at"],
            "removed_at": r["removed_at"]}


def history(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM watchlist ORDER BY added_at, id").fetchall()
    names = queries.stock_names(conn, sorted({r["code"] for r in rows}))
    return [{**_row(r), **names[r["code"]]} for r in rows]


def _bars(conn: sqlite3.Connection, code5: str, n: int) -> list[tuple[str, float]]:
    rows = conn.execute("SELECT date, adj_close FROM daily_bars WHERE code=? AND adj_close IS NOT NULL"
                        " ORDER BY date DESC LIMIT ?", (code5, n)).fetchall()
    return [(r[0], float(r[1])) for r in reversed(rows)]


def list_payload(conn: sqlite3.Connection, sort: str = "added", today: str | None = None) -> dict:
    from .memos import list_memos

    cfg = rules()
    today = today or queries.today_jst()
    rows = [_row(r) for r in _active_rows(conn)]
    names = queries.stock_names(conn, [r["code"] for r in rows], today)
    memo_items = list_memos(conn, limit=1000, today=today)["items"]
    items = []
    for r in rows:
        head = queries.stock_header(conn, r["code"], today)
        price = head["price"] if head and head["state"] == "ok" else None
        al = financials.alerts(financials.Fins(conn, r["code"]))
        memos = [m for m in memo_items if m["code"] == r["code"]]
        items.append({
            **r, **names[r["code"]],
            "price": None if price is None else {k: price[k] for k in ("as_of", "close", "change", "change_pct",
                                                                       "prev_date", "change_unavailable_reason")},
            "alerts": {"count": len(al["items"]), "as_of": al["as_of"], "judged": al["as_of"] is not None},
            "memos": {"count": len(memos), "due": sum(1 for m in memos if m["due"])},
        })
    if sort == "code":
        items.sort(key=lambda x: x["code"])
    elif sort == "tag":
        order = {t: i for i, t in enumerate(cfg["tags"])}
        items.sort(key=lambda x: (min((order[t] for t in x["tags"]), default=len(order)), x["added_at"]))
    return {"max": cfg["watchlist_max"], "max_provisional": cfg["watchlist_max_provisional"], "count": len(items),
            "sort": sort if sort in SORTS else "added", "sorts": list(SORTS), "tags": cfg["tags"],
            "reason_max_len": cfg["reason_max_len"], "items": items}


# --------------------------------------------------------------------------- digest

def _kind_label(row: sqlite3.Row) -> str:
    kind = row["doc_kind"] or fins.parse_doc_type(row["doc_type"])["doc_kind"]
    if kind == "statement" and row["period_type"] in ("1Q", "2Q", "3Q", "FY"):
        return render("digest.kind.statement", period=render(f"fin.period.{row['period_type']}"))
    if kind in ("forecast_revision", "dividend_revision"):
        return render(f"digest.kind.{kind}")
    return render("digest.kind.other")


def price_band(bars: list[tuple[str, float]], window: int, k: float) -> dict:
    """Latest day move vs k × population σ of the previous ``window`` daily returns."""
    need = window + 2
    if len(bars) < 2:
        return {"state": "no_prev", "n": len(bars), "need": need}
    (d0, c0), (d1, c1) = bars[-2], bars[-1]
    r = (c1 / c0 - 1.0) * 100.0 if c0 else None
    out = {"date": d1, "prev_date": d0, "change_pct": None if r is None else round(r, 6), "n": len(bars),
           "need": need, "window": window, "k": k}
    if r is None:
        return {**out, "state": "no_prev"}
    if len(bars) < need:
        return {**out, "state": "insufficient"}
    closes = [c for _, c in bars[-need:-1]]
    rets = [(closes[i] / closes[i - 1] - 1.0) * 100.0 for i in range(1, len(closes)) if closes[i - 1]]
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((x - mean) ** 2 for x in rets) / len(rets))
    band = k * sd
    beyond = round(abs(r), 6) > round(band, 6)
    return {**out, "state": "beyond" if beyond else "within", "sd_pct": round(sd, 6), "band_pct": round(band, 6)}


def _price_text(pb: dict) -> str:
    st = pb["state"]
    if st == "no_prev":
        return render("digest.price_no_prev", date=pb.get("date") or "—")
    if st == "insufficient":
        return render("digest.price_insufficient", pct=signed(pb["change_pct"], 2), date=pb["date"], need=pb["need"],
                      n=pb["n"])
    return render(f"digest.price_{st}", pct=signed(pb["change_pct"], 2), date=pb["date"], window=pb["window"],
                  sd=num(pb["sd_pct"], 2), k=num(pb["k"], 0), band=num(pb["band_pct"], 2))


def digest_item(conn: sqlite3.Connection, code5: str, name: str | None, display: str) -> dict:
    vol = rules()["volatility"]
    window, k = int(vol["window"]), float(vol["k"])
    bars = _bars(conn, code5, window + 2)
    base = {"code": code5, "display_code": display, "name": name}
    if not bars:
        return {**base, "as_of": None, "prev_date": None, "changed": False, "changes": [],
                "lines": [render("digest.no_bars")], "summary": render("digest.no_bars"), "price": None}
    as_of = bars[-1][0]
    prev = bars[-2][0] if len(bars) >= 2 else queries.previous_business_day(conn, as_of)
    pb = price_band(bars, window, k)
    changes: list[dict] = []
    if prev is not None:
        for r in conn.execute("SELECT disclosure_no, disclosed_date, doc_kind, doc_type, period_type FROM fins_summary"
                              " WHERE code=? AND disclosed_date > ? ORDER BY disclosed_date, disclosed_time,"
                              " disclosure_no", (code5, prev)):
            changes.append({"kind": "disclosure", "date": r["disclosed_date"], "disclosure_no": r["disclosure_no"],
                            "text": render("digest.disclosure", date=r["disclosed_date"], kind=_kind_label(r),
                                           no=r["disclosure_no"])})
        before = financials.alerts(financials.Fins(conn, code5, upto=prev))["items"]
        seen = {(a["rule"], a["text"]) for a in before}
        for a in financials.alerts(financials.Fins(conn, code5))["items"]:
            if (a["rule"], a["text"]) not in seen:
                changes.append({"kind": "alert", "rule": a["rule"], "date": a["as_of"],
                                "text": render("digest.alert_new", text=a["text"])})
    if pb["state"] == "beyond":
        changes.append({"kind": "price", "date": pb["date"], "text": _price_text(pb)})
    lines = [c["text"] for c in changes]
    if pb["state"] != "beyond":
        lines.append(_price_text(pb))
    if changes:
        summary = None
    elif prev is None:
        summary = render("digest.price_no_prev", date=as_of)
    elif pb["state"] == "within":
        summary = render("digest.nothing", prev=prev, window=window, date=as_of)
    else:
        summary = render("digest.nothing_no_band", prev=prev, date=as_of)
    return {**base, "as_of": as_of, "prev_date": prev, "changed": bool(changes), "changes": changes,
            "lines": lines, "summary": summary, "price": pb}


def digest(conn: sqlite3.Connection, today: str | None = None) -> dict:
    from .memos import due_memos

    today = today or queries.today_jst()
    vol = rules()["volatility"]
    codes = active_codes(conn)
    names = queries.stock_names(conn, codes, today)
    items = [digest_item(conn, c, names[c]["name"], names[c]["display_code"]) for c in codes]
    changed = sum(1 for i in items if i["changed"])
    if not items:
        summary = render("digest.empty")
    elif changed == 0:
        summary = render("digest.all_nothing", n=len(items))
    else:
        summary = render("digest.some_changed", n=len(items), changed=changed)
    due = [{"memo_id": m["memo_id"], "code": m["code"], "display_code": m["display_code"], "name": m["name"],
            "review_date": m["review_date"],
            "text": render("digest.memo_due", code=m["display_code"], name=m["name"] or "", created=m["created_at"][:10],
                           date=m["review_date"])} for m in due_memos(conn, today)]
    return {
        "today": today,
        "count": len(items),
        "changed_count": changed,
        "summary": summary,
        "items": items,
        "memo_due": {"items": due, "none_text": None if due else render("digest.memo_due_none", today=today)},
        "notes": [render("digest.note"), render("digest.note_band", window=vol["window"], k=vol["k"])],
    }


# --------------------------------------------------------------------------- nightly (要件 4.2 ⑨)

def queue_weekly_refetch(conn: sqlite3.Connection) -> list[str]:
    """Saturday: re-fetch the watchlist stocks' financial summaries by code
    (P3, 訂正検知). Goes through the shared rate-limited queue."""
    from .bars import enqueue
    from .jquants.ratelimit import Priority

    queued = []
    with conn:
        for c in active_codes(conn):
            if enqueue(conn, "fins_full", Priority.P3_REFETCH, code=c, reason="watchlist_weekly"):
                queued.append(c)
    return queued
