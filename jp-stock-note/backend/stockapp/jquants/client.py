"""J-Quants v2 client. Auth: API key from .env (JQUANTS_API_KEY) sent as the
``x-api-key`` header. All HTTP goes through the injected Transport via the
shared RequestQueue."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .ratelimit import (
    AUTH_ERROR,
    HTTP_ERROR,
    INTERNAL_ERROR,
    NETWORK_ERROR,
    NO_API_KEY,
    OK,
    PLAN_LIMIT,
    RATE_LIMITED,
    SERVER_ERROR,
    TIMEOUT,
    Attempt,
    Priority,
    RequestQueue,
)
from .transport import HttpRequest

ENDPOINT_MASTER = "/v2/equities/master"
ENDPOINT_CALENDAR = "/v2/markets/calendar"
ENDPOINT_DAILY_BARS = "/v2/equities/bars/daily"
ENDPOINT_TOPIX = "/v2/indices/bars/daily/topix"
ENDPOINT_FINS_SUMMARY = "/v2/fins/summary"

MAX_PAGES = 200  # safety valve against a pagination loop

REASONS = {
    NO_API_KEY: "APIキーが未設定です（.env の JQUANTS_API_KEY を設定してください）。通信は行っていません。",
    PLAN_LIMIT: "403 Forbidden：現在のプランでは利用できない可能性があります（プラン制限として記録。再試行しません）。",
    AUTH_ERROR: "401 Unauthorized：APIキーが無効か期限切れの可能性があります。",
    RATE_LIMITED: "429 Too Many Requests：レート制限に達しました（指数バックオフで3回再試行しても解消せず）。",
    SERVER_ERROR: "J-Quants側のサーバーエラー（5xx）：3回再試行しても解消しませんでした。",
    TIMEOUT: "タイムアウト：J-Quantsから応答がありませんでした（3回再試行後）。",
    NETWORK_ERROR: "通信エラー：J-Quantsに接続できませんでした（3回再試行後）。",
    HTTP_ERROR: "想定外のHTTPエラーです。",
    INTERNAL_ERROR: "内部エラー：送信処理で例外が発生しました（サーバーログを確認してください）。",
}


class JQuantsError(Exception):
    def __init__(self, outcome: str, status: int | None, reason: str, attempts: list[Attempt]):
        super().__init__(reason)
        self.outcome = outcome
        self.status = status
        self.reason = reason
        self.attempts = attempts


@dataclass
class FetchResult:
    rows: list[dict[str, Any]]
    attempts: list[Attempt] = field(default_factory=list)


class JQuantsClient:
    def __init__(self, api_key: str | None, queue: RequestQueue, base_url: str = "https://api.jquants.com"):
        self._api_key = api_key or None
        self.queue = queue
        self.base_url = base_url.rstrip("/")

    def __repr__(self) -> str:  # never show the key
        return f"JQuantsClient(base_url={self.base_url!r}, api_key_configured={self._api_key is not None})"

    def _scrub(self, text: str) -> str:
        if self._api_key and text:
            text = text.replace(self._api_key, "***")
        return text

    def get_all(
        self, path: str, params: Mapping[str, str] | None = None, priority: int = Priority.P4_BACKFILL
    ) -> FetchResult:
        """GET ``path`` and follow ``pagination_key`` until exhausted."""
        if not self._api_key:
            raise JQuantsError(NO_API_KEY, None, REASONS[NO_API_KEY], [])
        base_params = {k: str(v) for k, v in (params or {}).items() if v not in (None, "")}
        rows: list[dict[str, Any]] = []
        attempts: list[Attempt] = []
        pagination_key: str | None = None
        for _ in range(MAX_PAGES):
            q = dict(base_params)
            if pagination_key:
                q["pagination_key"] = pagination_key
            request = HttpRequest("GET", self.base_url + path, q, {"x-api-key": self._api_key})
            result = self.queue.execute(request, priority)
            attempts.extend(result.attempts)
            if result.outcome != OK:
                reason = REASONS.get(result.outcome, REASONS[HTTP_ERROR])
                if result.outcome == HTTP_ERROR and result.status is not None:
                    reason = f"HTTP {result.status}：{reason}"
                body = result.response.json_body if result.response else None
                if isinstance(body, dict) and body.get("message"):
                    reason += f"（APIメッセージ: {str(body['message'])[:200]}）"
                raise JQuantsError(result.outcome, result.status, self._scrub(reason), attempts)
            body = result.response.json_body if result.response else None
            if not isinstance(body, dict):
                raise JQuantsError(HTTP_ERROR, result.status, "応答がJSONオブジェクトではありません。", attempts)
            rows.extend(body.get("data") or [])
            pagination_key = body.get("pagination_key") or None
            if not pagination_key:
                return FetchResult(rows, attempts)
        raise JQuantsError(HTTP_ERROR, None, f"ページング上限（{MAX_PAGES}）を超えました。", attempts)

    def stock_master(self, date: str | None = None, priority: int = Priority.P4_BACKFILL) -> FetchResult:
        return self.get_all(ENDPOINT_MASTER, {"date": date} if date else None, priority)

    def trading_calendar(
        self, date_from: str | None = None, date_to: str | None = None, priority: int = Priority.P4_BACKFILL
    ) -> FetchResult:
        return self.get_all(ENDPOINT_CALENDAR, {"from": date_from, "to": date_to}, priority)

    def daily_bars(
        self,
        code: str | None = None,
        date: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        priority: int = Priority.P4_BACKFILL,
    ) -> FetchResult:
        """``code`` alone = that code's full available history; ``date`` alone =
        all codes for that day; ``code`` + ``from``/``to`` = a range for one code."""
        if not code and not date:
            raise ValueError("daily_bars needs code or date")
        return self.get_all(
            ENDPOINT_DAILY_BARS,
            {"code": code, "date": date, "from": date_from, "to": date_to},
            priority,
        )

    def fins_summary(
        self, code: str | None = None, date: str | None = None, priority: int = Priority.P4_BACKFILL
    ) -> FetchResult:
        """``code`` = every disclosure of that code within the plan's history;
        ``date`` = every code's disclosures on that day (nightly batch)."""
        if not code and not date:
            raise ValueError("fins_summary needs code or date")
        return self.get_all(ENDPOINT_FINS_SUMMARY, {"code": code, "date": date}, priority)

    def topix_bars(
        self, date_from: str | None = None, date_to: str | None = None, priority: int = Priority.P4_BACKFILL
    ) -> FetchResult:
        return self.get_all(ENDPOINT_TOPIX, {"from": date_from, "to": date_to}, priority)
