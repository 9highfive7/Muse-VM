"""Token bucket / request queue (要件 4.3, 受け入れ条件④)."""
from __future__ import annotations

import pytest

from stockapp.jquants.ratelimit import (
    PLAN_LIMIT,
    RATE_LIMITED,
    SERVER_ERROR,
    TIMEOUT,
    Priority,
    RequestQueue,
    TokenBucket,
)
from stockapp.jquants.transport import FakeClock, HttpRequest, HttpResponse, TransportTimeout


def max_in_any_window(times: list[float], window: float = 60.0) -> int:
    """Max number of events inside any half-open window [t, t+window).
    Checking windows that start at each event is sufficient."""
    times = sorted(times)
    best, j = 0, 0
    for i, start in enumerate(times):
        while j < len(times) and times[j] < start + window:
            j += 1
        best = max(best, j - i)
    return best


def req(i: int = 0) -> HttpRequest:
    return HttpRequest("GET", "https://example.invalid/v2/x", {"i": str(i)})


@pytest.mark.parametrize("capacity", [1, 5, 50])
def test_100_queued_requests_never_exceed_50_per_60s(fake_transport_cls, capacity):
    clock = FakeClock()
    transport = fake_transport_cls(clock=clock)
    queue = RequestQueue(transport, TokenBucket(rate_per_minute=50, capacity=capacity, clock=clock), clock=clock)

    jobs = [queue.submit(req(i), priority=Priority.P4_BACKFILL) for i in range(100)]
    queue.drain()

    assert all(j.result is not None and j.result.ok for j in jobs)
    assert len(transport.sent_at) == 100
    assert max_in_any_window(transport.sent_at) <= 50
    # Also robust to float edge effects: a window a hair wider than 60 s
    # may contain at most one extra send, never a burst.
    assert max_in_any_window(transport.sent_at, 60.0 - 1e-6) <= 50
    # 100 requests at 50/min must take at least one full minute of (fake) time.
    assert transport.sent_at[-1] - transport.sent_at[0] >= 60.0 - 1e-6


def test_limit_holds_with_interleaved_idle_periods(fake_transport_cls):
    clock = FakeClock()
    transport = fake_transport_cls(clock=clock)
    queue = RequestQueue(transport, TokenBucket(rate_per_minute=50, capacity=50, clock=clock), clock=clock)
    for burst in range(4):
        for i in range(30):
            queue.submit(req(i))
        queue.drain()
        clock.advance(17.0)  # idle gap lets tokens refill partially
    assert len(transport.sent_at) == 120
    assert max_in_any_window(transport.sent_at) <= 50


def test_priority_order_p1_before_p4(fake_transport_cls):
    clock = FakeClock()
    transport = fake_transport_cls(clock=clock)
    queue = RequestQueue(transport, TokenBucket(clock=clock), clock=clock)
    queue.submit(HttpRequest("GET", "https://x.invalid/p4"), Priority.P4_BACKFILL)
    queue.submit(HttpRequest("GET", "https://x.invalid/p2"), Priority.P2_NIGHTLY)
    queue.submit(HttpRequest("GET", "https://x.invalid/p1"), Priority.P1_ON_DEMAND)
    queue.submit(HttpRequest("GET", "https://x.invalid/p4b"), Priority.P4_BACKFILL)
    queue.drain()
    assert [r.url.rsplit("/", 1)[1] for r in transport.requests] == ["p1", "p2", "p4", "p4b"]


def _queue(transport, clock):
    return RequestQueue(transport, TokenBucket(clock=clock), clock=clock, backoff_base_seconds=2.0)


def test_429_retries_three_times_with_exponential_backoff(fake_transport_cls):
    clock = FakeClock()
    transport = fake_transport_cls(clock=clock, responses=[HttpResponse(429)] * 4)
    result = _queue(transport, clock).execute(req())
    assert result.outcome == RATE_LIMITED
    assert len(result.attempts) == 4  # 1 + 3 retries
    assert clock.sleeps == [2.0, 4.0, 8.0]


def test_5xx_then_success(fake_transport_cls):
    clock = FakeClock()
    transport = fake_transport_cls(
        clock=clock, responses=[HttpResponse(503), HttpResponse(500), HttpResponse(200, {"data": []})]
    )
    result = _queue(transport, clock).execute(req())
    assert result.ok
    assert [a.outcome for a in result.attempts] == [SERVER_ERROR, SERVER_ERROR, "ok"]


def test_403_is_not_retried(fake_transport_cls):
    clock = FakeClock()
    transport = fake_transport_cls(clock=clock, responses=[HttpResponse(403), HttpResponse(200)])
    result = _queue(transport, clock).execute(req())
    assert result.outcome == PLAN_LIMIT
    assert len(transport.requests) == 1
    assert clock.sleeps == []


def test_timeout_retried_then_reported(fake_transport_cls):
    clock = FakeClock()
    transport = fake_transport_cls(clock=clock, responses=[TransportTimeout("t")] * 4)
    result = _queue(transport, clock).execute(req())
    assert result.outcome == TIMEOUT
    assert len(transport.requests) == 4


def test_retry_after_header_is_respected(fake_transport_cls):
    clock = FakeClock()
    transport = fake_transport_cls(
        clock=clock, responses=[HttpResponse(429, headers={"Retry-After": "30"}), HttpResponse(200, {"data": []})]
    )
    result = _queue(transport, clock).execute(req())
    assert result.ok
    assert clock.sleeps == [30.0]


def test_retries_also_go_through_the_bucket(fake_transport_cls):
    """Retries count against the 50/min budget like any other send."""
    clock = FakeClock()
    transport = fake_transport_cls(clock=clock, handler=lambda r: HttpResponse(503))
    queue = RequestQueue(transport, TokenBucket(capacity=50, clock=clock), clock=clock, backoff_base_seconds=0.001)
    for i in range(30):
        queue.submit(req(i))
    queue.drain()
    assert len(transport.sent_at) == 120
    assert max_in_any_window(transport.sent_at) <= 50


def test_unexpected_transport_exception_is_recorded_and_queue_survives(fake_transport_cls):
    clock = FakeClock()
    transport = fake_transport_cls(clock=clock, responses=[ValueError("boom"), HttpResponse(200, {"data": []})])
    queue = _queue(transport, clock)
    first = queue.execute(req(1))
    assert first.outcome == "error" and len(first.attempts) == 1  # not retried
    assert queue.execute(req(2)).ok
