"""Phase 4: 構造化判断メモ (要件 3.10 / 7.3).

* A memo is the USER's own record. The app validates completeness only; it
  never pre-fills, suggests or evaluates a stance (区分) or any entry.
* Storage is append-only: ``memo`` / ``memo_revision`` / ``memo_snapshot`` /
  ``memo_review`` reject UPDATE and DELETE with SQLite triggers (0005).
  Editing = a new revision; old revisions stay readable.
* Each revision captures an immutable snapshot of the key values the stock
  page shows at that moment (header price, stage facts, card sentences,
  valuation, macro headline values), serialised as canonical JSON with a
  SHA-256. ``verify_memo`` recomputes every hash and reports tampering.
* The self-evaluation (プロセス × 結果) is a later annotation row; it never
  touches the revision or its snapshot.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import date
from functools import lru_cache
from typing import Any

from . import financials, macro, queries, technicals
from .db import utc_now
from .queries import resolve_code
from .wording import CONTENT_DIR

FORM_PATH = CONTENT_DIR / "memo_form.json"
SNAPSHOT_SCHEMA = 1
# 要件 3.10 自動スナップショット: these five are always recorded (value or reason).
CORE_KEYS = ("close", "per", "equity_ratio", "topix", "usdjpy")


class MemoError(ValueError):
    """Validation error (HTTP 422)."""


class MemoNotFound(LookupError):
    """Unknown memo / stock (HTTP 404)."""


@lru_cache(maxsize=1)
def form() -> dict:
    data = json.loads(FORM_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- validation

def _text(v: Any, max_len: int, label: str, required: bool = True) -> str:
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise MemoError(f"{label}は文字列で入力してください。")
    t = v.strip()
    if required and not t:
        raise MemoError(f"{label}を入力してください。")
    if len(t) > max_len:
        raise MemoError(f"{label}は{max_len}文字以内で入力してください。")
    return t


def _keys(options: list[dict]) -> list[str]:
    return [o["key"] for o in options]


def validate_content(payload: Any) -> dict:
    """Returns the normalised content or raises MemoError naming what is missing.
    Required (要件 3.10 / 受け入れ条件①): stance, ≥1 checked item with a note in
    each of チャート／財務／市場環境, both scenario texts, ≥1 review condition."""
    if not isinstance(payload, dict):
        raise MemoError("入力内容が読み取れません。")
    f = form()
    stance = payload.get("stance")
    if stance not in _keys(f["stance"]["options"]):
        raise MemoError("区分（監視／保有／見送り）を自分で選んでください。")

    ev_cfg = f["evidence"]
    items_by_area = {a["key"]: set(_keys(a["items"])) for a in ev_cfg["areas"]}
    raw_ev = payload.get("evidence")
    if not isinstance(raw_ev, list):
        raise MemoError("根拠チェックリストが読み取れません。")
    evidence, seen = [], set()
    for e in raw_ev:
        if not isinstance(e, dict):
            raise MemoError("根拠チェックリストが読み取れません。")
        area, item = e.get("area"), e.get("item")
        if area not in items_by_area or item not in items_by_area[area]:
            raise MemoError("根拠チェックリストの項目は選択肢から選んでください。")
        if (area, item) in seen:
            raise MemoError("根拠チェックリストに同じ項目が重複しています。")
        seen.add((area, item))
        label = next(i["label"] for a in ev_cfg["areas"] if a["key"] == area for i in a["items"] if i["key"] == item)
        evidence.append({"area": area, "item": item,
                         "note": _text(e.get("note"), ev_cfg["note_max_len"], f"「{label}」の所見")})
    missing = [a["label"] for a in ev_cfg["areas"] if not any(e["area"] == a["key"] for e in evidence)]
    if missing:
        raise MemoError(f"根拠チェックリストは3分野それぞれ1項目以上が必要です（未入力：{'・'.join(missing)}）。")

    sc_cfg = f["scenario"]
    sc = payload.get("scenario")
    if not isinstance(sc, dict):
        raise MemoError("想定シナリオを入力してください。")
    scenario = {"expected": _text(sc.get("expected"), sc_cfg["max_len"], "想定シナリオ（想定どおりの場合）"),
                "different": _text(sc.get("different"), sc_cfg["max_len"], "想定シナリオ（想定と異なる場合）")}

    c_cfg = f["conditions"]
    raw_c = payload.get("conditions")
    if not isinstance(raw_c, list):
        raise MemoError("見直し条件を1件以上入力してください。")
    conditions = []
    for c in raw_c:
        if not isinstance(c, dict):
            raise MemoError("見直し条件が読み取れません。")
        text = _text(c.get("text"), c_cfg["max_len"], "見直し条件", required=False)
        if not text:
            continue   # blank rows of the form are ignored
        kind = c.get("kind")
        if kind not in _keys(c_cfg["kinds"]):
            raise MemoError("見直し条件の種類は選択肢から選んでください。")
        conditions.append({"kind": kind, "text": text})
    if not conditions:
        raise MemoError("見直し条件を1件以上入力してください。")
    if len(conditions) > c_cfg["max_items"]:
        raise MemoError(f"見直し条件は{c_cfg['max_items']}件までです。")

    review_date = payload.get("review_date") or None
    if review_date is not None:
        try:
            if not isinstance(review_date, str) or len(review_date) != 10:
                raise ValueError
            date.fromisoformat(review_date)
        except ValueError:
            raise MemoError("見直し日はYYYY-MM-DDの形式で入力してください。") from None
    return {"stance": stance, "evidence": evidence, "scenario": scenario, "conditions": conditions,
            "review_date": review_date}


def validate_review(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise MemoError("入力内容が読み取れません。")
    sr = form()["self_review"]
    process, result = payload.get("process"), payload.get("result")
    if process not in _keys(sr["process"]["options"]):
        raise MemoError("プロセスの自己評価を選んでください。")
    if result not in _keys(sr["result"]["options"]):
        raise MemoError("結果の自己評価を選んでください。")
    return {"process": process, "result": result,
            "note": _text(payload.get("note"), sr["note_max_len"], "振り返りメモ", required=False)}


# --------------------------------------------------------------------------- snapshot capture

def _num(v: Any, digits: int = 6) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, digits)


def _field(key: str, label: str, group: str, value: Any, as_of: str | None, unit: str, digits: int,
           reason: str | None = None, core: bool = False) -> dict:
    v = _num(value)
    return {"key": key, "label": label, "group": group, "value": v, "available": v is not None,
            "as_of": as_of, "unit": unit, "digits": digits,
            "reason": None if v is not None else (reason or "算出不可"), "core": core}


def _texts(key: str, label: str, lines: list[str]) -> dict:
    return {"key": key, "label": label, "lines": [ln for ln in lines if ln]}


def capture_values(conn: sqlite3.Connection, code: str) -> dict:
    """The key values the stock page shows right now, as plain JSON (DB only)."""
    code5 = resolve_code(code)
    head = queries.stock_header(conn, code5)
    if head is None:
        raise MemoNotFound("銘柄マスタに該当する銘柄がありません。")
    fields: list[dict] = []
    texts: list[dict] = []

    # --- header (要件 2.3)
    price = head["price"] if head["state"] == "ok" else None
    no_bars = "株価データ（日足）が未取得"
    fields.append(_field("close", "終値（調整後）", "header", price and price["close"], price and price["as_of"],
                         "円", 1, no_bars, core=True))
    fields.append(_field("change_pct", "前日比", "header", price and price["change_pct"], price and price["as_of"],
                         "%", 2, (price and price["change_unavailable_reason"]) or no_bars))

    # --- technical stages (①〜④) and the price-position card
    tech = technicals.technicals_payload(conn, code5, "3m")
    tv, t_as_of = tech["values"], tech["as_of"]
    short = "日足の本数が不足"
    for key, label, unit, digits in (
            ("dev25", "25日移動平均線との乖離率", "%", 2), ("dev75", "75日移動平均線との乖離率", "%", 2),
            ("dev200", "200日移動平均線との乖離率", "%", 2), ("rsi", "RSI(14)", "", 1), ("pct_b", "%B", "", 2),
            ("macd_hist", "MACDヒストグラム", "", 2), ("from_high_pct", "52週高値からの距離", "%", 2),
            ("range_pos", "52週レンジ内の位置（下端0・上端100）", "", 1)):
        fields.append(_field(f"tech.{key}", label, "technical", tv.get(key), t_as_of, unit, digits, short))
    s4 = tech["stage4"]
    ex60 = next((r for r in s4["excess_returns"] if r["n"] == 60), None)
    fields.append(_field("tech.excess60", "過去60営業日の対TOPIX超過リターン", "technical",
                         ex60 and ex60["excess_pt"], ex60 and ex60["as_of"], "ポイント", 1,
                         "日足またはTOPIXの本数が不足"))
    fields.append(_field("tech.beta", "ベータ（250営業日）", "technical", s4["beta"]["beta"], s4["beta"]["as_of"], "", 2,
                         "有効な観測数が不足"))
    card = technicals.price_position_card(conn, code5)
    texts.append(_texts("card.price_position", "価格位置カード", [s["text"] for s in card.get("sentences", [])]))
    for st in ("stage1", "stage2", "stage3", "stage4"):
        texts.append(_texts(st, tech[st]["title"], [f["text"] for f in tech[st]["facts"]]))

    # --- financials / valuation (要件 3.4〜3.7)
    f = financials.Fins(conn, code5)
    val = financials.valuation_payload(conn, code5)
    for key, label, digits in (("per", "予想PER", 2), ("pbr", "PBR（推定）", 2), ("dividend_yield", "予想配当利回り", 2)):
        m = val[key]
        fields.append(_field(key, label, "valuation", m["value"], m["as_of"], "倍" if key != "dividend_yield" else "%",
                             digits, m["reason"], core=key == "per"))
        if key in ("per", "pbr"):
            p = m.get("percentile") or {}
            fields.append(_field(f"{key}_percentile", f"{label}の自社過去レンジ内パーセンタイル", "valuation",
                                 p.get("percentile") if p.get("state") == "ok" else None, m["as_of"], "", 0,
                                 "観測不足" if m["value"] is not None else m["reason"]))
    ra = val["roe"]["actual"]
    fields.append(_field("roe", "ROE（実績）", "valuation", ra["value"], ra["as_of"], "%", 1, ra["reason"]))
    pf = val["payout"]["forecast"]
    fields.append(_field("payout_forecast", "予想配当性向", "valuation", pf["value"], pf["as_of"], "%", 1, pf["reason"]))
    e = f.get("equity_ratio") if not f.empty else {"state": "no_data"}
    eq_ok = e.get("state") == "ok"
    fields.append(_field("equity_ratio", "自己資本比率", "financial", e.get("current_pct") if eq_ok else None,
                         e.get("disclosed_date") if eq_ok else f.as_of, "%", 1,
                         "決算サマリーが未取得" if f.empty else "自己資本比率が開示されていない", core=True))
    fields.append(_field("equity_ratio_diff", "自己資本比率の前年同期差", "financial",
                         e.get("diff_pt") if eq_ok else None, e.get("disclosed_date") if eq_ok else f.as_of,
                         "ポイント", 1, "前年同期の値がない"))
    al = financials.alerts(f)
    texts.append(_texts("alerts", "確認ポイント", [a["text"] for a in al["items"]] or [al.get("none_text") or ""]))
    bc = financials.business_cards(conn, code5)
    for c in bc["cards"]:
        texts.append(_texts(f"card.{c['key']}", c["title"], [s["text"] for s in c["sentences"]]))

    # --- macro headline values (要件 3.8)
    mp = macro.macro_payload(conn, code5)
    it = mp["items"]
    for key, label, unit, digits, core in (("topix", "TOPIX", "", 2, True), ("usdjpy", "ドル円", "円", 2, True),
                                           ("us10y", "米10年金利", "%", 2, False),
                                           ("us_spread", "米長短スプレッド（10年−2年）", "%ポイント", 2, False),
                                           ("jp10y", "日本10年金利（月次）", "%", 2, False)):
        x = it[key]
        fields.append(_field(key, label, "macro", x.get("value"), x.get("as_of"), unit, digits,
                             f"{label}のデータが未取得", core=core))
    fx = it["usdjpy"]
    tk = fx.get("tankan") or {}
    fields.append(_field("usdjpy_tankan_gap", "ドル円と短観の想定レートの差", "macro",
                         tk.get("gap_yen") if tk.get("available") else None, fx.get("as_of"), "円", 2,
                         "ドル円のデータが未取得"))
    texts.append(_texts("macro", "マクロ帯", [it[k]["facts"][0]["text"] for k in ("topix", "us10y", "us_spread",
                                                                               "usdjpy", "jp10y")
                                            if it[k].get("facts")]))
    return {
        "schema": SNAPSHOT_SCHEMA,
        "code": code5,
        "name": head["stock"]["name"],
        "captured_date_jst": queries.today_jst(),
        "fields": fields,
        "texts": texts,
    }


# --------------------------------------------------------------------------- hashing

def _revision_hash(memo_id: int, revision_no: int, created_at: str, review_date: str | None, content_json: str) -> str:
    return sha256_hex(canonical({"memo_id": memo_id, "revision_no": revision_no, "created_at": created_at,
                                 "review_date": review_date, "content_json": content_json}))


def _snapshot_hash(memo_id: int, revision_id: int, code: str, captured_at: str, values_json: str) -> str:
    return sha256_hex(canonical({"memo_id": memo_id, "revision_id": revision_id, "code": code,
                                 "captured_at": captured_at, "values_json": values_json}))


def _review_hash(memo_id: int, revision_id: int, created_at: str, content_json: str) -> str:
    return sha256_hex(canonical({"memo_id": memo_id, "revision_id": revision_id, "created_at": created_at,
                                 "content_json": content_json}))


# --------------------------------------------------------------------------- writes

def _insert_revision(conn: sqlite3.Connection, memo_id: int, code5: str, content: dict, values: dict,
                     created_at: str) -> int:
    no = conn.execute("SELECT COALESCE(MAX(revision_no), 0) + 1 FROM memo_revision WHERE memo_id=?",
                      (memo_id,)).fetchone()[0]
    content_json = canonical(content)
    rev_id = conn.execute(
        "INSERT INTO memo_revision(memo_id, revision_no, content_json, review_date, created_at, content_sha256)"
        " VALUES (?,?,?,?,?,?)",
        (memo_id, no, content_json, content["review_date"], created_at,
         _revision_hash(memo_id, no, created_at, content["review_date"], content_json)),
    ).lastrowid
    values_json = canonical(values)
    conn.execute(
        "INSERT INTO memo_snapshot(revision_id, memo_id, code, captured_at, values_json, sha256) VALUES (?,?,?,?,?,?)",
        (rev_id, memo_id, code5, created_at, values_json,
         _snapshot_hash(memo_id, rev_id, code5, created_at, values_json)),
    )
    return rev_id


def create_memo(conn: sqlite3.Connection, code: str, payload: Any) -> dict:
    content = validate_content(payload)
    code5 = resolve_code(code)
    values = capture_values(conn, code5)   # raises MemoNotFound for an unknown code
    now = utc_now()
    with conn:
        memo_id = conn.execute("INSERT INTO memo(code, created_at) VALUES (?, ?)", (code5, now)).lastrowid
        _insert_revision(conn, memo_id, code5, content, values, now)
    return get_memo(conn, memo_id)


def add_revision(conn: sqlite3.Connection, memo_id: int, payload: Any) -> dict:
    row = _memo_row(conn, memo_id)
    content = validate_content(payload)
    values = capture_values(conn, row["code"])
    with conn:
        _insert_revision(conn, memo_id, row["code"], content, values, utc_now())
    return get_memo(conn, memo_id)


def add_review(conn: sqlite3.Connection, memo_id: int, payload: Any) -> dict:
    _memo_row(conn, memo_id)
    content = validate_review(payload)
    rev_id = payload.get("revision_id") if isinstance(payload, dict) else None
    if rev_id is None:
        rev_id = conn.execute("SELECT MAX(revision_id) FROM memo_revision WHERE memo_id=?", (memo_id,)).fetchone()[0]
    elif not isinstance(rev_id, int) or isinstance(rev_id, bool) or conn.execute(
            "SELECT 1 FROM memo_revision WHERE memo_id=? AND revision_id=?", (memo_id, rev_id)).fetchone() is None:
        raise MemoError("振り返りの対象リビジョンが見つかりません。")
    now = utc_now()
    content_json = canonical(content)
    with conn:
        conn.execute(
            "INSERT INTO memo_review(memo_id, revision_id, content_json, created_at, content_sha256) VALUES (?,?,?,?,?)",
            (memo_id, rev_id, content_json, now, _review_hash(memo_id, rev_id, now, content_json)),
        )
    return get_memo(conn, memo_id)


# --------------------------------------------------------------------------- reads

def _memo_row(conn: sqlite3.Connection, memo_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM memo WHERE memo_id=?", (memo_id,)).fetchone()
    if row is None:
        raise MemoNotFound("判断メモが見つかりません。")
    return row


def _snapshot_dict(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    return {"snapshot_id": r["snapshot_id"], "captured_at": r["captured_at"], "sha256": r["sha256"],
            "values": json.loads(r["values_json"])}


def _revision_dict(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    snap = conn.execute("SELECT * FROM memo_snapshot WHERE revision_id=?", (r["revision_id"],)).fetchone()
    return {"revision_id": r["revision_id"], "revision_no": r["revision_no"], "created_at": r["created_at"],
            "content": json.loads(r["content_json"]), "content_sha256": r["content_sha256"],
            "snapshot": _snapshot_dict(snap)}


def _review_dict(r: sqlite3.Row) -> dict:
    return {"review_id": r["review_id"], "revision_id": r["revision_id"], "created_at": r["created_at"],
            **json.loads(r["content_json"])}


def get_memo(conn: sqlite3.Connection, memo_id: int) -> dict:
    m = _memo_row(conn, memo_id)
    revs = [_revision_dict(conn, r) for r in conn.execute(
        "SELECT * FROM memo_revision WHERE memo_id=? ORDER BY revision_no", (memo_id,))]
    reviews = [_review_dict(r) for r in conn.execute(
        "SELECT * FROM memo_review WHERE memo_id=? ORDER BY review_id", (memo_id,))]
    names = queries.stock_names(conn, [m["code"]])
    return {"memo_id": m["memo_id"], "code": m["code"], **names[m["code"]], "created_at": m["created_at"],
            "revisions": revs, "latest": revs[-1] if revs else None, "reviews": reviews,
            "verification": verify_memo(conn, memo_id)}


def _due(conn: sqlite3.Connection, memo_id: int, review_date: str | None, today: str) -> bool:
    """要件 3.10: 見直し日が到来 = review date reached and no self-review has
    been added on/after that date."""
    if not review_date or review_date > today:
        return False
    done = conn.execute("SELECT 1 FROM memo_review WHERE memo_id=? AND substr(created_at, 1, 10) >= ? LIMIT 1",
                        (memo_id, review_date)).fetchone()
    return done is None


def list_memos(conn: sqlite3.Connection, code: str | None = None, limit: int = 100, today: str | None = None) -> dict:
    today = today or queries.today_jst()
    sql = ("SELECT m.memo_id, m.code, m.created_at, r.revision_no, r.created_at AS revised_at, r.content_json,"
           " r.review_date, (SELECT COUNT(*) FROM memo_review v WHERE v.memo_id = m.memo_id) AS review_count"
           " FROM memo m JOIN memo_revision r ON r.memo_id = m.memo_id"
           " AND r.revision_no = (SELECT MAX(revision_no) FROM memo_revision WHERE memo_id = m.memo_id)")
    args: list[Any] = []
    if code is not None:
        sql += " WHERE m.code = ?"
        args.append(resolve_code(code))
    sql += " ORDER BY r.created_at DESC, m.memo_id DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    names = queries.stock_names(conn, sorted({r["code"] for r in rows}))
    stance_labels = {o["key"]: o["label"] for o in form()["stance"]["options"]}
    items = []
    for r in rows:
        c = json.loads(r["content_json"])
        items.append({"memo_id": r["memo_id"], "code": r["code"], **names[r["code"]], "created_at": r["created_at"],
                      "revision_count": r["revision_no"], "revised_at": r["revised_at"], "stance": c["stance"],
                      "stance_label": stance_labels.get(c["stance"]), "review_date": r["review_date"],
                      "review_count": r["review_count"], "due": _due(conn, r["memo_id"], r["review_date"], today)})
    return {"today": today, "count": len(items), "items": items}


def due_memos(conn: sqlite3.Connection, today: str | None = None) -> list[dict]:
    return [m for m in list_memos(conn, limit=1000, today=today)["items"] if m["due"]]


# --------------------------------------------------------------------------- review (snapshot vs current)

def comparison(snapshot_values: dict, current_values: dict) -> dict:
    cur_fields = {f["key"]: f for f in current_values["fields"]}
    rows = []
    for s in snapshot_values["fields"]:
        c = cur_fields.get(s["key"])
        diff = None
        if c is not None and s["value"] is not None and c["value"] is not None:
            diff = round(c["value"] - s["value"], 6)
        pick = ("value", "available", "as_of", "reason")
        rows.append({"key": s["key"], "label": s["label"], "group": s["group"], "unit": s["unit"],
                     "digits": s["digits"], "core": s["core"],
                     "snapshot": {k: s[k] for k in pick},
                     "current": {k: c[k] for k in pick} if c else {"value": None, "available": False, "as_of": None,
                                                                   "reason": "現在は算出していない項目"},
                     "diff": diff})
    cur_texts = {t["key"]: t for t in current_values["texts"]}
    texts = [{"key": t["key"], "label": t["label"], "snapshot": t["lines"],
              "current": cur_texts[t["key"]]["lines"] if t["key"] in cur_texts else []}
             for t in snapshot_values["texts"]]
    return {"rows": rows, "texts": texts}


def review_payload(conn: sqlite3.Connection, memo_id: int, revision_id: int | None = None) -> dict:
    memo = get_memo(conn, memo_id)
    revs = memo["revisions"]
    rev = revs[0] if revision_id is None else next((r for r in revs if r["revision_id"] == revision_id), None)
    if rev is None:
        raise MemoNotFound("リビジョンが見つかりません。")
    snap = rev["snapshot"]
    current = capture_values(conn, memo["code"])
    cmp = comparison(snap["values"], current) if snap else {"rows": [], "texts": []}
    return {
        "memo_id": memo_id, "code": memo["code"], "display_code": memo["display_code"], "name": memo["name"],
        "revision": {k: rev[k] for k in ("revision_id", "revision_no", "created_at", "content")},
        "revisions": [{"revision_id": r["revision_id"], "revision_no": r["revision_no"],
                       "created_at": r["created_at"]} for r in revs],
        "snapshot_captured_at": snap and snap["captured_at"],
        "current_computed_at": utc_now(),
        **cmp,
        "reviews": [v for v in memo["reviews"] if v["revision_id"] == rev["revision_id"]],
        "verification": memo["verification"],
        "notes": [form()["review_view"]["note"], form()["review_view"]["core_note"]],
    }


# --------------------------------------------------------------------------- verification

def verify_memo(conn: sqlite3.Connection, memo_id: int) -> dict:
    """Recompute every SHA-256 of one memo. ok=False lists what does not match
    (changed row, missing snapshot, gap in the revision numbers)."""
    problems: list[dict] = []
    checked = 0
    revs = conn.execute("SELECT * FROM memo_revision WHERE memo_id=? ORDER BY revision_no", (memo_id,)).fetchall()
    for i, r in enumerate(revs, 1):
        checked += 1
        if r["revision_no"] != i:
            problems.append({"table": "memo_revision", "id": r["revision_id"], "reason": "リビジョン番号に欠番がある"})
        if _revision_hash(memo_id, r["revision_no"], r["created_at"], r["review_date"], r["content_json"]) \
                != r["content_sha256"]:
            problems.append({"table": "memo_revision", "id": r["revision_id"], "reason": "内容のハッシュが一致しない"})
        s = conn.execute("SELECT * FROM memo_snapshot WHERE revision_id=?", (r["revision_id"],)).fetchone()
        if s is None:
            problems.append({"table": "memo_snapshot", "id": None, "reason": f"リビジョン{r['revision_no']}のスナップショットがない"})
            continue
        checked += 1
        if s["memo_id"] != memo_id or _snapshot_hash(memo_id, r["revision_id"], s["code"], s["captured_at"],
                                                       s["values_json"]) != s["sha256"]:
            problems.append({"table": "memo_snapshot", "id": s["snapshot_id"], "reason": "スナップショットのハッシュが一致しない"})
    if not revs:
        problems.append({"table": "memo_revision", "id": None, "reason": "リビジョンがない"})
    for v in conn.execute("SELECT * FROM memo_review WHERE memo_id=?", (memo_id,)):
        checked += 1
        if _review_hash(memo_id, v["revision_id"], v["created_at"], v["content_json"]) != v["content_sha256"]:
            problems.append({"table": "memo_review", "id": v["review_id"], "reason": "振り返りのハッシュが一致しない"})
    return {"ok": not problems, "checked": checked, "problems": problems,
            "text": "改変なし（保存時のハッシュと一致）" if not problems else "保存時のハッシュと一致しない記録がある"}


def verify_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> bool:
    s = conn.execute("SELECT * FROM memo_snapshot WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    if s is None:
        return False
    return _snapshot_hash(s["memo_id"], s["revision_id"], s["code"], s["captured_at"], s["values_json"]) == s["sha256"]


def verify_all(conn: sqlite3.Connection) -> dict:
    ids = [r[0] for r in conn.execute("SELECT memo_id FROM memo ORDER BY memo_id")]
    results = {i: verify_memo(conn, i) for i in ids}
    orphans = conn.execute("SELECT COUNT(*) FROM memo_snapshot WHERE revision_id NOT IN"
                           " (SELECT revision_id FROM memo_revision)").fetchone()[0]
    bad = [i for i, r in results.items() if not r["ok"]]
    return {"ok": not bad and not orphans, "memos": len(ids), "tampered_memo_ids": bad, "orphan_snapshots": orphans,
            "note": "アプリ経由では変更・削除できません。ハッシュの照合で改変を検知しますが、"
                    "ローカルのデータファイルを直接書き換える操作そのものは防げません。"}


# --------------------------------------------------------------------------- export (要件 7.3)

def export_all(conn: sqlite3.Connection) -> dict:
    from . import watchlist

    memos = [get_memo(conn, r[0]) for r in conn.execute("SELECT memo_id FROM memo ORDER BY memo_id")]
    return {"exported_at": utc_now(), "memos": memos, "watchlist": watchlist.history(conn),
            "verification": verify_all(conn)}


def _fmt(v: float | None, digits: int) -> str:
    return "—" if v is None else f"{v:,.{digits}f}"


def export_markdown(conn: sqlite3.Connection) -> str:
    data = export_all(conn)
    f = form()
    stance = {o["key"]: o["label"] for o in f["stance"]["options"]}
    items = {i["key"]: i["label"] for a in f["evidence"]["areas"] for i in a["items"]}
    areas = {a["key"]: a["label"] for a in f["evidence"]["areas"]}
    kinds = {k["key"]: k["label"] for k in f["conditions"]["kinds"]}
    proc = {o["key"]: o["label"] for o in f["self_review"]["process"]["options"]}
    res = {o["key"]: o["label"] for o in f["self_review"]["result"]["options"]}
    out = [f"# 判断メモ・ウォッチリスト エクスポート（{data['exported_at']}）", "", f["notice"], ""]
    out += ["## ウォッチリスト", ""]
    for w in data["watchlist"]:
        status = f"解除 {w['removed_at']}" if w["removed_at"] else "監視中"
        tags = "・".join(w["tags"]) if w["tags"] else "—"
        out.append(f"- {w['display_code']} {w['name'] or ''}：{w['reason']}（タグ：{tags}／追加 {w['added_at']}／{status}）")
    out += ["", "## 判断メモ", ""]
    for m in data["memos"]:
        out += [f"### メモ#{m['memo_id']} {m['display_code']} {m['name'] or ''}（作成 {m['created_at']}）", ""]
        for r in m["revisions"]:
            c = r["content"]
            out += [f"#### リビジョン{r['revision_no']}（{r['created_at']}）", "",
                    f"- 区分（自分の記録）：{stance.get(c['stance'], c['stance'])}"]
            for e in c["evidence"]:
                out.append(f"- 根拠［{areas[e['area']]}／{items.get(e['item'], e['item'])}］：{e['note']}")
            out += [f"- 想定どおりの場合：{c['scenario']['expected']}", f"- 想定と異なる場合：{c['scenario']['different']}"]
            for cond in c["conditions"]:
                out.append(f"- 見直し条件［{kinds.get(cond['kind'], cond['kind'])}］：{cond['text']}")
            out.append(f"- 見直し日：{c['review_date'] or '—'}")
            if r["snapshot"]:
                out += ["", f"スナップショット（{r['snapshot']['captured_at']}、SHA-256 {r['snapshot']['sha256']}）", "",
                        "| 項目 | 値 | 基準日 |", "|---|---|---|"]
                for fld in r["snapshot"]["values"]["fields"]:
                    val = (f"{_fmt(fld['value'], fld['digits'])}{fld['unit']}" if fld["available"]
                           else f"算出不可（{fld['reason']}）")
                    out.append(f"| {fld['label']} | {val} | {fld['as_of'] or '—'} |")
            out.append("")
        for v in m["reviews"]:
            out.append(f"- 振り返り（{v['created_at']}、リビジョンID {v['revision_id']}）：プロセス＝{proc.get(v['process'])}、"
                       f"結果＝{res.get(v['result'])}{'、' + v['note'] if v['note'] else ''}")
        out += ["", f"改変検知：{m['verification']['text']}", ""]
    return "\n".join(out) + "\n"
