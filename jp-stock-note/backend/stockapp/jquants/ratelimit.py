"""Rate limiting and the single, prioritised request queue (要件 4.3).

* ``TokenBucket`` paces sends at 50/minute. A small burst capacity keeps an
  on-demand page load snappy, and a sliding-window guard makes the cap hard:
  no 60-second window (half-open, [t, t+60)) ever contains more than 50 sends,
  regardless of burst capacity.
* ``RequestQueue`` is the only path to the transport. It sends one request at
  a time, in priority order (P1 first), retrying 429/5xx/timeouts with
  exponential backoff up to 3 times and never retrying 403.
"""
from __future__ import annotations

import heapq
import itertools
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from .transport import Clock, HttpRequest, HttpResponse, SystemClock, Transport, TransportError, TransportTimeout

log = logging.getLogger(__name__)
_EPS = 1e-9


class Priority(IntEnum):
    P1_ON_DEMAND = 1   # user is waiting on screen
    P2_NIGHTLY = 2     # nightly batch, today's data
    P3_REFETCH = 3     # split re-fetch, correction detection
    P4_BACKFILL = 4    # catch-up, master refresh


class TokenBucket:
    def __init__(
        self,
        rate_per_minute: int = 50,
        capacity: int = 5,
        clock: Clock | None = None,
        window_seconds: float = 60.0,
    ):
        if rate_per_minute <= 0 or capacity <= 0:
            raise ValueError("rate and capacity must be positive")
        self.limit = rate_per_minute
        self.window = window_seconds
        self.rate = rate_per_minute / window_seconds  # tokens per second
        self.capacity = float(min(capacity, rate_per_minute))
        self.clock = clock or SystemClock()
        self._tokens = self.capacity
        self._last = self.clock.monotonic()
        self._sent: deque[float] = deque()
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now

    def _wait_needed(self, now: float) -> float:
        while self._sent and self._sent[0] <= now - self.window:
            self._sent.popleft()
        wait = 0.0
        # Epsilon: refill arithmetic can land on 0.99999999…; the resulting
        # sub-ulp sleep would never advance the clock. The sliding-window
        # check below still enforces the hard 50/60s cap exactly.
        if self._tokens < 1.0 - _EPS:
            wait = (1.0 - self._tokens) / self.rate
        if len(self._sent) >= self.limit:
            wait = max(wait, self._sent[0] + self.window - now)
        return wait

    def acquire(self) -> float:
        """Block (via clock.sleep) until a send is allowed. Returns the send time."""
        with self._lock:
            for _ in range(100_000):  # guard: a stuck clock must fail loudly, not spin
                now = self.clock.monotonic()
                self._refill(now)
                wait = self._wait_needed(now)
                if wait <= 0:
                    self._tokens = max(0.0, self._tokens - 1.0)
                    self._sent.append(now)
                    return now
                self.clock.sleep(wait)
            raise RuntimeError("rate limiter could not acquire a token (clock not advancing?)")


# Outcome codes recorded in fetch_log.result / api_request_log.outcome.
OK = "ok"
PLAN_LIMIT = "plan_limit"
RATE_LIMITED = "rate_limited"
SERVER_ERROR = "server_error"
TIMEOUT = "timeout"
NETWORK_ERROR = "network_error"
AUTH_ERROR = "auth_error"
HTTP_ERROR = "http_error"
NO_API_KEY = "no_api_key"
INTERNAL_ERROR = "error"

RETRYABLE = {RATE_LIMITED, SERVER_ERROR, TIMEOUT, NETWORK_ERROR}


@dataclass
class Attempt:
    attempt: int
    sent_at: float          # wall clock epoch seconds
    status: int | None
    outcome: str
    elapsed_ms: int


@dataclass
class JobResult:
    outcome: str
    status: int | None
    response: HttpResponse | None
    attempts: list[Attempt]

    @property
    def ok(self) -> bool:
        return self.outcome == OK


@dataclass(order=True)
class _Job:
    priority: int
    seq: int
    request: HttpRequest = field(compare=False)
    done: threading.Event = field(compare=False, default_factory=threading.Event)
    result: JobResult | None = field(compare=False, default=None)


def classify(status: int) -> str:
    if 200 <= status < 300:
        return OK
    if status == 403:
        return PLAN_LIMIT
    if status == 401:
        return AUTH_ERROR
    if status == 429:
        return RATE_LIMITED
    if status >= 500:
        return SERVER_ERROR
    return HTTP_ERROR


class RequestQueue:
    """Single serialized sender with a priority heap.

    Callers use ``execute()``: the job is queued, then the caller takes the
    sender lock and processes queued jobs highest-priority-first until its own
    job is done ("caller runs"). This keeps exactly one in-flight request and
    needs no background thread, which also makes it deterministic under a
    fake clock. ``submit()`` + ``drain()`` expose the same mechanics for bulk
    or test use.
    """

    def __init__(
        self,
        transport: Transport,
        limiter: TokenBucket | None = None,
        clock: Clock | None = None,
        max_retries: int = 3,
        backoff_base_seconds: float = 2.0,
        backoff_cap_seconds: float = 60.0,
    ):
        self.transport = transport
        self.clock = clock or (limiter.clock if limiter else SystemClock())
        self.limiter = limiter or TokenBucket(clock=self.clock)
        self.max_retries = max_retries
        self.backoff_base = backoff_base_seconds
        self.backoff_cap = backoff_cap_seconds
        self._heap: list[_Job] = []
        self._heap_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._seq = itertools.count()

    def submit(self, request: HttpRequest, priority: int = Priority.P4_BACKFILL) -> _Job:
        job = _Job(int(priority), next(self._seq), request)
        with self._heap_lock:
            heapq.heappush(self._heap, job)
        return job

    def pending(self) -> int:
        with self._heap_lock:
            return len(self._heap)

    def _pop(self) -> _Job | None:
        with self._heap_lock:
            return heapq.heappop(self._heap) if self._heap else None

    def drain(self) -> None:
        with self._send_lock:
            while (job := self._pop()) is not None:
                self._process(job)

    def execute(self, request: HttpRequest, priority: int = Priority.P4_BACKFILL) -> JobResult:
        job = self.submit(request, priority)
        while not job.done.is_set():
            with self._send_lock:
                if job.done.is_set():
                    break
                nxt = self._pop()
                if nxt is not None:
                    self._process(nxt)
                    continue
            # Our job was popped by another caller that holds the lock.
            job.done.wait(0.05)
        assert job.result is not None
        return job.result

    def _backoff(self, attempt: int, response: HttpResponse | None) -> float:
        delay = self.backoff_base * (2 ** (attempt - 1))
        if response is not None:
            retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
            try:
                delay = max(delay, float(retry_after)) if retry_after else delay
            except ValueError:
                pass
        return min(delay, self.backoff_cap)

    def _process(self, job: _Job) -> None:
        attempts: list[Attempt] = []
        outcome, status, response = HTTP_ERROR, None, None
        for attempt in range(1, self.max_retries + 2):
            self.limiter.acquire()
            sent_wall = self.clock.time()
            t0 = self.clock.monotonic()
            response = None
            try:
                response = self.transport.send(job.request)
                status = response.status
                outcome = classify(status)
            except TransportTimeout:
                status, outcome = None, TIMEOUT
            except TransportError:
                status, outcome = None, NETWORK_ERROR
            except Exception as exc:  # never let one job break the queue
                log.warning("jquants transport raised %s", type(exc).__name__)
                status, outcome = None, INTERNAL_ERROR
            elapsed = int((self.clock.monotonic() - t0) * 1000)
            attempts.append(Attempt(attempt, sent_wall, status, outcome, elapsed))
            # Log path + status only: never headers or full URLs with params.
            log.info("jquants %s attempt=%d status=%s outcome=%s", _path(job.request.url), attempt, status, outcome)
            if outcome not in RETRYABLE or attempt > self.max_retries:
                break
            self.clock.sleep(self._backoff(attempt, response))
        job.result = JobResult(outcome, status, response, attempts)
        job.done.set()


def _path(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).path


__all__: list[Any] = [
    "Priority",
    "TokenBucket",
    "RequestQueue",
    "JobResult",
    "Attempt",
    "classify",
]
