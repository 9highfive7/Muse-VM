"""Records the API responses for the static demo build (GitHub Pages).

    .venv/bin/python demo/record_fixtures.py        # from app/

What it does
------------
1. Creates a throw-away SQLite DB in a temp dir (never touches data/app.db).
2. Starts the real FastAPI app (stockapp.main.create_app) with a fake J-Quants
   transport and a fake FRED transport that serve *synthetic* data only:
   the test stock master (tests/fixtures/master.json), a weekday trading
   calendar, deterministic random-walk daily bars for 7203 / 7267 and TOPIX,
   five fiscal years of financial summaries for 7203
   (tests/fins_fixtures.march_company) and wave-shaped FRED series.
   No network access, no real API key (a placeholder string is used and the
   recorded responses are checked not to contain it).
3. Drives the app the way the screens do (data fetch, ウォッチリスト登録,
   判断メモ作成・リビジョン追記・振り返り, 短観レート入力, S0 確認) and
   records every GET the frontend makes, plus the on-demand POSTs that a
   stock page sends when it opens (ensure-history / ensure-fins, recorded in
   their "already stored" state).
4. Writes frontend/src/demo/fixtures.json:
   {"meta": {...}, "routes": {"GET /api/...": {"status": 200, "body": ...}},
    "stocks": [search rows], "export": {"json": ..., "md": "..."}}

All numbers are synthetic. Company names come from the test stock master.
"""
from __future__ import annotations

import json
import math
import random
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

APP = Path(__file__).resolve().parents[1]
BACKEND = APP / "backend"
TESTS = BACKEND / "tests"
OUT = APP / "frontend" / "src" / "demo" / "fixtures.json"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(TESTS))

from conftest import FakeTransport, load_fixture  # noqa: E402
from fins_fixtures import march_company  # noqa: E402
from fred_fixtures import FredHandler, daily_dates, make_fred_client, months  # noqa: E402
from stockapp import fred, technical_view, wording  # noqa: E402
from stockapp.config import Settings  # noqa: E402
from stockapp.jquants.client import JQuantsClient  # noqa: E402
from stockapp.jquants.ratelimit import RequestQueue, TokenBucket  # noqa: E402
from stockapp.jquants.transport import FakeClock, HttpResponse  # noqa: E402

PLACEHOLDER_KEY = "demo-placeholder-not-a-key"
LAST_DAY = "2026-11-20"           # last synthetic trading day (Fri)
BARS_FROM = "2025-03-03"          # ~1年8か月 of daily bars
STOCK_PAGES = ["72030", "72670"]  # stock pages recorded in full
RANGES = ["3m", "6m", "1y", "3y", "5y"]
SEARCH_QUERIES = ["7203", "72030", "トヨタ", "toyota", "7267", "ホンダ", "本田", "自動車", "9433", "9984",
                  "ソフト", "KDDI", "1", "7", "存在しない銘柄"]

# Synthetic holiday list for the demo calendar (weekday closures only).
HOLIDAYS = {
    "2025-03-20", "2025-04-29", "2025-05-05", "2025-05-06", "2025-07-21", "2025-08-11", "2025-09-15",
    "2025-09-23", "2025-10-13", "2025-11-03", "2025-11-24", "2025-12-31", "2026-01-01", "2026-01-02",
    "2026-01-12", "2026-02-11", "2026-02-23", "2026-03-20", "2026-04-29", "2026-05-04", "2026-05-05",
    "2026-05-06", "2026-07-20", "2026-08-11", "2026-09-21", "2026-09-22", "2026-09-23", "2026-10-12",
    "2026-11-03", "2026-11-23", "2026-12-31",
}


def all_days(start: str, end: str) -> list[str]:
    d, e = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    while d <= e:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def is_business(day: str) -> bool:
    return date.fromisoformat(day).weekday() < 5 and day not in HOLIDAYS


BUSINESS_DAYS = [d for d in all_days(BARS_FROM, LAST_DAY) if is_business(d)]


def walk(seed: int, start: float, end: float, vol: float, last_move: float | None = None) -> list[float]:
    """Deterministic random walk over BUSINESS_DAYS, rescaled to end at ``end``."""
    rng = random.Random(seed)
    n = len(BUSINESS_DAYS)
    lv = [start]
    for i in range(1, n):
        r = rng.gauss(0.0002, vol) + 0.004 * math.sin(i / 37.0) * vol * 10
        if last_move is not None and i == n - 1:
            r = last_move
        lv.append(lv[-1] * (1 + r))
    scale = end / lv[-1]
    return [x * scale for x in lv]


def r1(x: float) -> float:
    return round(x, 1)


def stock_bars(code: str, closes: list[float], shares: float, seed: int) -> list[dict]:
    rng = random.Random(seed)
    rows, prev = [], closes[0]
    for day, c in zip(BUSINESS_DAYS, closes):
        c = r1(c)
        o = prev * (1 + rng.uniform(-0.004, 0.004))
        h = max(o, c) * (1 + rng.uniform(0, 0.009))
        lo = min(o, c) * (1 - rng.uniform(0, 0.009))
        vol = float(int(rng.uniform(0.6, 1.6) * 5_000_000))
        rows.append({
            "Date": day, "Code": code, "O": r1(o), "H": r1(h), "L": r1(lo), "C": c, "UL": "0", "LL": "0",
            "Vo": vol, "Va": float(round(vol * c)), "AdjFactor": 1.0,
            "AdjO": r1(o), "AdjH": r1(h), "AdjL": r1(lo), "AdjC": c, "AdjVo": vol,
            "MktCap": round(c * shares / 1e6, 1),   # 百万円 (docs/api-field-mapping.md)
        })
        prev = c
    return rows


def topix_bars() -> list[dict]:
    rng = random.Random(7)
    closes = walk(20261122, 2650.0, 2905.3, 0.008)
    rows, prev = [], closes[0]
    for day, c in zip(BUSINESS_DAYS, closes):
        o = prev * (1 + rng.uniform(-0.003, 0.003))
        rows.append({"Date": day, "O": round(o, 2), "H": round(max(o, c) * 1.003, 2),
                     "L": round(min(o, c) * 0.997, 2), "C": round(c, 2)})
        prev = c
    return rows


def calendar_rows() -> list[dict]:
    return [{"Date": d, "HolDiv": "1" if is_business(d) else "0"} for d in all_days("2025-01-01", "2026-12-31")]


def master_rows() -> list[dict]:
    # drop the test-only "翌営業日版" row (it exists to test date selection)
    return [r for r in load_fixture("master.json")["data"] if r["Date"] == "2026-11-20"]


class SyntheticJQuants:
    """FakeTransport handler for the five J-Quants endpoints the app calls."""

    def __init__(self):
        self.bars = {
            "72030": stock_bars("72030", walk(7203, 2480.0, 3051.0, 0.012, last_move=0.031), 1.0e8, 1),
            "72670": stock_bars("72670", walk(7267, 1610.0, 1500.0, 0.013), 5.0e8, 2),
        }
        self.topix = topix_bars()
        self.fins = {"72030": march_company("72030")}
        self.calls: list[str] = []

    def __call__(self, request):
        path = urlsplit(request.url).path
        p = request.params
        self.calls.append(f"{path}?{'&'.join(f'{k}={v}' for k, v in sorted(p.items()))}")

        def rng(rows, key="Date"):
            lo, hi = p.get("from"), p.get("to")
            return [r for r in rows if (not lo or r[key] >= lo) and (not hi or r[key] <= hi)]

        if path == "/v2/equities/master":
            data = master_rows()
        elif path == "/v2/markets/calendar":
            data = rng(calendar_rows())
        elif path == "/v2/equities/bars/daily":
            if "code" in p:
                data = rng(self.bars.get(p["code"], []))
            else:
                data = [r for rows in self.bars.values() for r in rows if r["Date"] == p.get("date")]
        elif path == "/v2/indices/bars/daily/topix":
            data = rng(self.topix)
        elif path == "/v2/fins/summary":
            if "code" in p:
                data = self.fins.get(p["code"], [])
            else:
                data = [r for rows in self.fins.values() for r in rows if r["DiscDate"] == p.get("date")]
        else:
            return HttpResponse(404, {"message": "not found"})
        return HttpResponse(200, {"data": data})


def fred_bodies() -> dict[str, list[tuple[str, object]]]:
    us = daily_dates("2026-11-19", 320)
    fx = daily_dates("2026-11-13", 320)
    jp = months("2005-11-01", 253)          # ... 2026-11-01

    def wave(dates, base, amp, period, trend, digits):
        return [(d, round(base + amp * math.sin(i / period) + trend * i, digits)) for i, d in enumerate(dates)]

    return {
        "DGS10": wave(us, 4.05, 0.22, 33.0, 0.0006, 2),
        "DGS2": wave(us, 3.70, 0.18, 29.0, -0.0004, 2),
        "DEXJPUS": wave(fx, 146.0, 3.5, 41.0, 0.012, 2),
        "IRLTLT01JPM156N": wave(jp, 0.45, 0.05, 6.0, 0.0045, 3),
        "IRSTCI01JPM156N": [(d, round(max(0.0, -0.05 + 0.0025 * i), 3)) for i, d in enumerate(jp)],
    }


# --------------------------------------------------------------------------- recording

def main() -> None:
    from fastapi.testclient import TestClient

    from stockapp.main import create_app

    tmp = Path(tempfile.mkdtemp(prefix="stockapp-demo-"))
    settings = Settings(db_path=tmp / "demo.db", frontend_dist=tmp / "dist", _api_key=None)
    jq_handler = SyntheticJQuants()
    clock = FakeClock()
    queue = RequestQueue(FakeTransport(clock=clock, handler=jq_handler), TokenBucket(clock=clock), clock=clock,
                         backoff_base_seconds=1.0)
    jq = JQuantsClient(PLACEHOLDER_KEY, queue, "https://jq.example.invalid")
    fred_client, _, _ = make_fred_client(FredHandler(fred_bodies()))
    app = create_app(settings, client=jq, fred_client=fred_client)
    c = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))

    routes: dict[str, dict] = {}

    def ok(r, what):
        assert r.status_code == 200, (what, r.status_code, r.text[:300])
        return r.json()

    def rec(method: str, path: str, *, must_ok: bool = True):
        r = c.request(method, path)
        if must_ok:
            assert r.status_code == 200, (method, path, r.status_code, r.text[:300])
        routes[f"{method} {path}"] = {"status": r.status_code, "body": r.json()}
        return r.json()

    # S0 before the confirmation (the demo shows S0 first, like the real app)
    first_run_before = ok(c.get("/api/settings/first-run"), "first-run")

    # ---- data, through the app's own fetch layer
    ok(c.post("/api/fetch/calendar"), "calendar")
    ok(c.post("/api/fetch/master"), "master")
    out = ok(c.post("/api/fetch-fred"), "fred")
    assert out["result"] == "ok", out
    ok(c.post("/api/settings/tankan", json={"value": 145.0, "survey_label": "日銀短観 2026年9月調査・全産業（サンプル値）"}),
       "tankan")
    for code in STOCK_PAGES:
        ok(c.post(f"/api/stocks/{code}/ensure-history"), "ensure-history")
        ok(c.post(f"/api/stocks/{code}/ensure-fins"), "ensure-fins")

    # ---- ウォッチリスト・判断メモ (synthetic user records)
    ok(c.post("/api/watchlist", json={"code": "7203", "reason": "次回決算で通期予想の扱いを確認する（サンプル）",
                                      "tags": ["決算待ち", "為替感応の観察"]}), "watch 7203")
    ok(c.post("/api/watchlist", json={"code": "7267", "reason": "75日移動平均線との位置関係を観察する（サンプル）",
                                      "tags": ["値動きの推移確認"]}), "watch 7267")
    memo_a = ok(c.post("/api/stocks/72030/memos", json={
        "stance": "watch",
        "evidence": [
            {"area": "chart", "item": "stage1", "note": "25日・75日・200日移動平均線の配列を確認した（サンプル）"},
            {"area": "financial", "item": "fin.progress", "note": "第2四半期累計の進捗率を過去3年と並べて確認した（サンプル）"},
            {"area": "macro", "item": "macro.usdjpy", "note": "ドル円と短観の想定レートの差を確認した（サンプル）"},
        ],
        "scenario": {"expected": "次回決算で通期予想が据え置かれる（サンプル）",
                     "different": "通期予想が修正される（サンプル）"},
        "conditions": [{"kind": "next_earnings", "text": "次回決算開示時に見直す"},
                       {"kind": "price_indicator", "text": "終値が75日移動平均線を下回ったとき"}],
        "review_date": "2026-11-20",
    }), "memo a")
    memo_a = ok(c.post(f"/api/memos/{memo_a['memo_id']}/revisions", json={
        "stance": "watch",
        "evidence": [
            {"area": "chart", "item": "card.price_position", "note": "価格位置カードの4文を読み直した（サンプル）"},
            {"area": "financial", "item": "card.performance", "note": "業績方向カードを確認した（サンプル）"},
            {"area": "macro", "item": "macro.rates", "note": "米10年金利と日本10年金利の推移を確認した（サンプル）"},
        ],
        "scenario": {"expected": "次回決算で通期予想が据え置かれる（サンプル）",
                     "different": "通期予想が修正される（サンプル）"},
        "conditions": [{"kind": "next_earnings", "text": "次回決算開示時に見直す"},
                       {"kind": "date", "text": "2027年2月の第3四半期決算後"}],
        "review_date": "2027-02-15",
    }), "memo a rev2")
    first_rev = memo_a["revisions"][0]["revision_id"]
    ok(c.post(f"/api/memos/{memo_a['memo_id']}/reviews", json={
        "revision_id": first_rev, "process": "followed", "result": "unclear",
        "note": "見直し条件に沿って画面を確認した。結果はまだどちらとも言えない（サンプル）"}), "review")
    ok(c.post("/api/stocks/72670/memos", json={
        "stance": "pass",
        "evidence": [
            {"area": "chart", "item": "stage4", "note": "TOPIXとの相対比較を確認した（サンプル）"},
            {"area": "financial", "item": "alerts", "note": "決算データが未取得であることを確認した（サンプル）"},
            {"area": "macro", "item": "macro.sector", "note": "業種の感応タグを確認した（サンプル）"},
        ],
        "scenario": {"expected": "決算データの取得後に改めて確認する（サンプル）", "different": "特になし（サンプル）"},
        "conditions": [{"kind": "other", "text": "決算データが表示されたとき"}],
        "review_date": "2026-11-01",
    }), "memo b")

    # ---- record: global screens
    rec("POST", "/api/settings/first-run")          # S0 確認 (the demo keeps it per browser tab)
    after = rec("GET", "/api/settings/first-run")
    assert first_run_before["required"] and not after["required"], (first_run_before, after)
    routes["GET /api/settings/first-run#before-ack"] = {"status": 200, "body": first_run_before}
    rec("GET", "/api/about")
    rec("GET", "/api/macro")
    rec("GET", "/api/digest")
    for s in ("added", "code", "tag"):
        rec("GET", f"/api/watchlist?sort={s}")
    rec("GET", "/api/memos?limit=1000")
    rec("GET", "/api/memos/verify")
    rec("GET", "/api/memo-form")
    rec("GET", "/api/settings/tankan")
    memo_ids = [m["memo_id"] for m in c.get("/api/memos?limit=1000").json()["items"]]
    for mid in memo_ids:
        detail = rec("GET", f"/api/memos/{mid}")
        rec("GET", f"/api/memos/{mid}/review")
        for rv in detail["revisions"]:
            rec("GET", f"/api/memos/{mid}/review?revision_id={rv['revision_id']}")

    # ---- record: stock pages (state after the first visit -> "stored")
    for code in STOCK_PAGES:
        e = rec("POST", f"/api/stocks/{code}/ensure-history")
        assert e["state"] == "stored", e
        f = rec("POST", f"/api/stocks/{code}/ensure-fins")
        assert f.get("state") != "failed", f
        rec("GET", f"/api/stocks/{code}/header")
        for r in RANGES:
            rec("GET", f"/api/stocks/{code}/chart?range={r}")
            rec("GET", f"/api/stocks/{code}/technicals?range={r}")
        rec("GET", f"/api/stocks/{code}/cards/price-position")
        for ind_key in technical_view.INDICATORS:     # 「テクニカルで見る」 pulldown
            rec("GET", f"/api/stocks/{code}/technical-backtest?indicator={ind_key}")
        rec("GET", f"/api/stocks/{code}/financials")
        rec("GET", f"/api/stocks/{code}/valuation")
        rec("GET", f"/api/stocks/{code}/cards/business")
        rec("GET", f"/api/stocks/{code}/macro")
        rec("GET", f"/api/stocks/{code}/memos")
        rec("GET", f"/api/watchlist/{code}")

    # ---- glossary (all terms; with each recorded code and without one)
    terms = list(dict.fromkeys([*wording.glossary(), *wording.fin_glossary(), *wording.macro_glossary()]))
    for t in terms:
        rec("GET", f"/api/glossary/{quote(t, safe='')}")
        for code in STOCK_PAGES:
            rec("GET", f"/api/glossary/{quote(t, safe='')}?code={code}")

    # ---- search: stock rows for client-side matching + reference answers
    stocks = {}
    for row in master_rows():
        for s in c.get(f"/api/search?q={row['Code']}").json()["results"]:
            stocks[s["code"]] = s
    for q in SEARCH_QUERIES:
        rec("GET", f"/api/search?q={quote(q, safe='')}")

    # status last: it reflects every fetch above
    rec("GET", "/api/status")

    export = {"json": c.get("/api/export?format=json").json(), "md": c.get("/api/export?format=md").text}

    # The macro/status payloads carry FRED series-page links (source_url) that
    # no screen renders. The demo data is not from FRED and the static bundle
    # must not contain the FRED host (same check as run_tests.sh), so drop them.
    # Likewise the endpoint name "fredgraph" (status table / macro source note)
    # is shortened, because run_tests.sh rejects it in browser bundles.
    def scrub(obj):
        if isinstance(obj, dict):
            return {k: (None if k == "source_url" else scrub(v)) for k, v in obj.items()}
        if isinstance(obj, list):
            return [scrub(v) for v in obj]
        if isinstance(obj, str):
            return obj.replace("/graph/fredgraph.csv", "FRED CSV（デモ版では省略）").replace("fredgraph.csv", "CSV")
        return obj

    routes = scrub(routes)
    export = scrub(export)

    payload = {
        "meta": {
            "note": "Synthetic demo data recorded from the real backend by demo/record_fixtures.py. Not real market data.",
            "recorded_on": date.today().isoformat(),
            "stock_pages": STOCK_PAGES,
            "ranges": RANGES,
            "bars_from": BARS_FROM,
            "bars_to": LAST_DAY,
            "jquants_calls": len(jq_handler.calls),
        },
        "routes": routes,
        "stocks": sorted(stocks.values(), key=lambda s: s["code"]),
        "export": export,
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    # hygiene: no key, no external hosts, no banned wording beyond what the server itself emits
    # ("jquants" alone stays: it is the source tag of fetch-log rows, not a host or key)
    for needle in (PLACEHOLDER_KEY, "api.jquants.com", "JQUANTS_API_KEY", "fred.stlouisfed.org", "fredgraph"):
        assert needle.lower() not in text.lower(), f"{needle!r} in recorded fixtures"
    assert "x-api-key" not in text.lower()
    # 「テクニカルで見る」 (technical-backtest) is the one block allowed to say 「勝率」
    # (要件 6.1 の例外, wording.TECH_VIEW_ALLOWED); every other recorded route is
    # checked against the full list as before.
    exception = {k: v for k, v in routes.items() if "/technical-backtest?" in k}
    others = {k: v for k, v in routes.items() if k not in exception}
    bad = wording.find_banned(json.dumps({**payload, "routes": others}, ensure_ascii=False))
    assert not bad, f"banned wording in fixtures: {bad}"
    bad = wording.find_banned(json.dumps(exception, ensure_ascii=False), allow=wording.TECH_VIEW_ALLOWED)
    assert not bad, f"banned wording in technical-backtest fixtures: {bad}"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(APP)}: {len(routes)} routes, {len(stocks)} stocks, {len(text) / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
