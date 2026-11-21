"""FastAPI app: /api/* plus the built React frontend, for 127.0.0.1 only."""
from __future__ import annotations

import ipaddress
import sqlite3
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Iterator

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import app_settings, bars, financials, fins, fred, ingest, macro, memos, queries, technicals, watchlist
from .config import Settings, load_settings
from .db import connect, migrate
from .jquants.client import JQuantsClient
from .jquants.ratelimit import Priority


def _is_loopback(host: str | None, allowed: frozenset[str]) -> bool:
    if not host:
        return False
    if host in allowed:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def create_app(settings: Settings | None = None, client: JQuantsClient | None = None,
               fred_client: "fred.FredClient | None" = None) -> FastAPI:
    settings = settings or load_settings()
    # Schema is created/upgraded on startup (same runner as `python -m stockapp.db`).
    boot = connect(settings.db_path)
    migrate(boot)
    boot.close()

    sync = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal sync
        if settings.background_jobs:
            from .scheduler import BackgroundSync

            sync = BackgroundSync(settings, get_client, get_fred_client)
            _app.state.sync = sync
            sync.start()  # startup catch-up, then nightly at 20:00 JST
        yield
        if sync is not None:
            sync.stop()

    app = FastAPI(title="jp-stock-beginner-app", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.settings = settings
    app.state.client = client
    app.state.fred_client = fred_client
    app.state.sync = None

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        peer = request.client.host if request.client else None
        if not _is_loopback(peer, settings.allowed_clients):
            return PlainTextResponse("forbidden: local access only", status_code=403)
        return await call_next(request)

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = connect(settings.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def get_client() -> JQuantsClient:
        if app.state.client is None:
            from .runtime import build_client

            app.state.client = build_client(settings)
        return app.state.client

    def get_fred_client() -> "fred.FredClient":
        if app.state.fred_client is None:
            from .runtime import build_fred_client

            app.state.fred_client = build_fred_client(settings)
        return app.state.fred_client

    @app.get("/api/search")
    def api_search(q: str = Query("", max_length=100), conn: sqlite3.Connection = Depends(get_conn)):
        return queries.search_stocks(conn, q)

    @app.get("/api/stocks/{code}/header")
    def api_header(code: str, conn: sqlite3.Connection = Depends(get_conn)):
        if len(code) > 12:
            raise HTTPException(400, "invalid code")
        result = queries.stock_header(conn, code)
        if result is None:
            raise HTTPException(404, "銘柄マスタに該当する銘柄がありません。")
        return result

    def _check_code(code: str) -> None:
        if len(code) > 12 or not code.strip():
            raise HTTPException(400, "invalid code")

    RANGE_PATTERN = "^(3m|6m|1y|3y|5y)$"

    @app.post("/api/stocks/{code}/ensure-history")
    def api_ensure_history(code: str, conn: sqlite3.Connection = Depends(get_conn)):
        """On-demand (P1): called when a stock page opens. Fetches the code's
        full daily-bar history (and TOPIX) only if not stored yet."""
        _check_code(code)
        out = bars.ensure_history(conn, get_client(), code)
        if app.state.sync is not None:
            app.state.sync.wake()
        return out

    @app.get("/api/stocks/{code}/chart")
    def api_chart(code: str, range: str = Query(technicals.DEFAULT_RANGE, pattern=RANGE_PATTERN),
                  conn: sqlite3.Connection = Depends(get_conn)):
        _check_code(code)
        return technicals.chart_payload(conn, code, range)

    @app.get("/api/stocks/{code}/technicals")
    def api_technicals(code: str, range: str = Query(technicals.DEFAULT_RANGE, pattern=RANGE_PATTERN),
                       conn: sqlite3.Connection = Depends(get_conn)):
        _check_code(code)
        return technicals.technicals_payload(conn, code, range)

    @app.get("/api/stocks/{code}/cards/price-position")
    def api_card_price_position(code: str, conn: sqlite3.Connection = Depends(get_conn)):
        _check_code(code)
        return technicals.price_position_card(conn, code)

    # ------------------------------------------------------------ Phase 2: financials
    @app.post("/api/stocks/{code}/ensure-fins")
    def api_ensure_fins(code: str, force: bool = Query(False), conn: sqlite3.Connection = Depends(get_conn)):
        """On-demand (P1): fetch the code's financial summaries when none are
        stored or the last by-code fetch is older than 7 days. ``force`` is
        the manual re-fetch (訂正検知の機会, 要件 4.6)."""
        _check_code(code)
        return fins.ensure_fins(conn, get_client(), code, force=force)

    @app.get("/api/stocks/{code}/financials")
    def api_financials(code: str, conn: sqlite3.Connection = Depends(get_conn)):
        _check_code(code)
        return financials.financials_payload(conn, code)

    @app.get("/api/stocks/{code}/valuation")
    def api_valuation(code: str, conn: sqlite3.Connection = Depends(get_conn)):
        _check_code(code)
        return financials.valuation_payload(conn, code)

    @app.get("/api/stocks/{code}/cards/business")
    def api_cards_business(code: str, conn: sqlite3.Connection = Depends(get_conn)):
        _check_code(code)
        return financials.business_cards(conn, code)

    # ------------------------------------------------------------ Phase 3: macro
    @app.get("/api/macro")
    def api_macro(conn: sqlite3.Connection = Depends(get_conn)):
        """S1 マクロ帯: every value carries as_of + cadence badge (要件 3.8)."""
        return macro.macro_payload(conn)

    @app.get("/api/stocks/{code}/macro")
    def api_stock_macro(code: str, conn: sqlite3.Connection = Depends(get_conn)):
        _check_code(code)
        out = macro.stock_macro_payload(conn, code)
        if out["state"] == "not_found":
            raise HTTPException(404, "銘柄マスタに該当する銘柄がありません。")
        return out

    @app.get("/api/settings/tankan")
    def api_get_tankan(conn: sqlite3.Connection = Depends(get_conn)):
        return app_settings.get_tankan(conn)

    @app.post("/api/settings/tankan")
    def api_set_tankan(payload: dict = Body(...), conn: sqlite3.Connection = Depends(get_conn)):
        """S5: 短観想定レートの手入力（四半期ごと, 要件 9.1-8）. JSON body only."""
        try:
            return app_settings.set_tankan(conn, payload.get("value"), payload.get("survey_label"))
        except app_settings.SettingError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/glossary/{term}")
    def api_glossary(term: str, code: str | None = Query(None, max_length=12),
                     conn: sqlite3.Connection = Depends(get_conn)):
        entry = technicals.glossary_entry(conn, term, code)
        if entry is None:
            raise HTTPException(404, "用語が見つかりません。")
        return entry

    @app.get("/api/status")
    def api_status(conn: sqlite3.Connection = Depends(get_conn)):
        out = queries.status(conn)
        out["queue"] = bars.pending_jobs(conn)
        out["api_key_configured"] = settings.api_key_configured  # boolean only, never the key
        out["tankan"] = app_settings.get_tankan(conn)
        out["fred_series"] = [dict(r) for r in conn.execute("SELECT * FROM fred_series ORDER BY series_id")]
        out["fred_series_ids"] = list(fred.SERIES_IDS)
        out["memo_verification"] = memos.verify_all(conn)
        out["first_run"] = app_settings.get_first_run(conn)
        out["counts"]["memos"] = conn.execute("SELECT COUNT(*) FROM memo").fetchone()[0]
        out["counts"]["watchlist_active"] = len(watchlist.active_codes(conn))
        return out

    @app.post("/api/fetch/{job}")
    def api_fetch(job: str, conn: sqlite3.Connection = Depends(get_conn)):
        """Manual trigger from S5 (runs through the shared rate-limited queue)."""
        client = get_client()
        if job == "master":
            return ingest.fetch_stock_master(conn, client, priority=Priority.P4_BACKFILL)
        if job == "calendar":
            return ingest.fetch_trading_calendar(conn, client, priority=Priority.P4_BACKFILL)
        raise HTTPException(404, "unknown job")

    @app.post("/api/fetch-fred")
    def api_fetch_fred(series: str | None = Query(None, max_length=40), conn: sqlite3.Connection = Depends(get_conn)):
        """Manual FRED fetch from S5 (all series, or one). Never needs a key."""
        if series is not None and series not in fred.SERIES_IDS:
            raise HTTPException(404, "unknown FRED series")
        return fred.fetch_all(conn, get_fred_client(), [series] if series else None, Priority.P1_ON_DEMAND)

    # ------------------------------------------------------------ Phase 4: memo / watchlist / S0
    def _memo_call(fn, *args):
        try:
            return fn(*args)
        except memos.MemoError as exc:
            raise HTTPException(422, str(exc)) from None
        except memos.MemoNotFound as exc:
            raise HTTPException(404, str(exc)) from None

    @app.get("/api/memo-form")
    def api_memo_form():
        """Form definition (choices only; nothing is pre-selected or suggested)."""
        return memos.form()

    @app.get("/api/stocks/{code}/memos")
    def api_stock_memos(code: str, conn: sqlite3.Connection = Depends(get_conn)):
        _check_code(code)
        return memos.list_memos(conn, code)

    @app.post("/api/stocks/{code}/memos")
    def api_create_memo(code: str, payload: dict = Body(...), conn: sqlite3.Connection = Depends(get_conn)):
        _check_code(code)
        return _memo_call(memos.create_memo, conn, code, payload)

    @app.get("/api/memos")
    def api_memos(limit: int = Query(100, ge=1, le=1000), conn: sqlite3.Connection = Depends(get_conn)):
        return memos.list_memos(conn, limit=limit)

    @app.get("/api/memos/verify")
    def api_memos_verify(conn: sqlite3.Connection = Depends(get_conn)):
        return memos.verify_all(conn)

    @app.get("/api/memos/{memo_id}")
    def api_memo(memo_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        return _memo_call(memos.get_memo, conn, memo_id)

    @app.post("/api/memos/{memo_id}/revisions")
    def api_memo_revision(memo_id: int, payload: dict = Body(...), conn: sqlite3.Connection = Depends(get_conn)):
        return _memo_call(memos.add_revision, conn, memo_id, payload)

    @app.get("/api/memos/{memo_id}/review")
    def api_memo_review(memo_id: int, revision_id: int | None = Query(None),
                        conn: sqlite3.Connection = Depends(get_conn)):
        return _memo_call(memos.review_payload, conn, memo_id, revision_id)

    @app.post("/api/memos/{memo_id}/reviews")
    def api_memo_add_review(memo_id: int, payload: dict = Body(...), conn: sqlite3.Connection = Depends(get_conn)):
        return _memo_call(memos.add_review, conn, memo_id, payload)

    @app.get("/api/export")
    def api_export(format: str = Query("json", pattern="^(json|md)$"), conn: sqlite3.Connection = Depends(get_conn)):
        """要件 7.3 エクスポート（JSON＋Markdown）: memos (all revisions, snapshots,
        self-reviews) and the watchlist history. Downloaded by the local browser only."""
        if format == "md":
            return PlainTextResponse(memos.export_markdown(conn), media_type="text/markdown; charset=utf-8",
                                     headers={"Content-Disposition": 'attachment; filename="stockapp-memos.md"'})
        return JSONResponse(memos.export_all(conn),
                            headers={"Content-Disposition": 'attachment; filename="stockapp-memos.json"'})

    def _watch_call(fn, *args):
        try:
            return fn(*args)
        except watchlist.WatchError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    @app.get("/api/watchlist")
    def api_watchlist(sort: str = Query("added", pattern="^(added|code|tag)$"),
                      conn: sqlite3.Connection = Depends(get_conn)):
        return watchlist.list_payload(conn, sort)

    @app.post("/api/watchlist")
    def api_watch_add(payload: dict = Body(...), conn: sqlite3.Connection = Depends(get_conn)):
        return _watch_call(watchlist.add, conn, payload.get("code"), payload.get("reason"), payload.get("tags"))

    @app.get("/api/watchlist/{code}")
    def api_watch_entry(code: str, conn: sqlite3.Connection = Depends(get_conn)):
        _check_code(code)
        return {"entry": watchlist.entry(conn, code), "max": watchlist.rules()["watchlist_max"],
                "count": len(watchlist.active_codes(conn)), "tags": watchlist.rules()["tags"],
                "reason_max_len": watchlist.rules()["reason_max_len"]}

    @app.delete("/api/watchlist/{code}")
    def api_watch_remove(code: str, conn: sqlite3.Connection = Depends(get_conn)):
        _check_code(code)
        return _watch_call(watchlist.remove, conn, code)

    @app.get("/api/digest")
    def api_digest(conn: sqlite3.Connection = Depends(get_conn)):
        """S1 大引け後ダイジェスト: factual changes since the previous business day."""
        return watchlist.digest(conn)

    @app.get("/api/about")
    def api_about(conn: sqlite3.Connection = Depends(get_conn)):
        return {**app_settings.about(), "first_run": app_settings.get_first_run(conn)}

    @app.get("/api/settings/first-run")
    def api_first_run(conn: sqlite3.Connection = Depends(get_conn)):
        return app_settings.get_first_run(conn)

    @app.post("/api/settings/first-run")
    def api_first_run_ack(conn: sqlite3.Connection = Depends(get_conn)):
        return app_settings.ack_first_run(conn)

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def api_not_found(rest: str):
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    dist: Path = settings.frontend_dist

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if not (dist / "index.html").is_file():
            return PlainTextResponse(
                "frontend is not built yet: run ./start.sh (or `npm run build` in frontend/)", status_code=503
            )
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")

    return app


def app_factory() -> FastAPI:
    return create_app(load_settings(background_jobs=True))
