"""Wires the production client: real httpx transport, system clock, one queue."""
from __future__ import annotations

from .config import Settings
from .jquants.client import JQuantsClient
from .jquants.ratelimit import RequestQueue, TokenBucket
from .jquants.transport import Clock, SystemClock, Transport


def build_client(settings: Settings, transport: Transport | None = None, clock: Clock | None = None) -> JQuantsClient:
    clock = clock or SystemClock()
    if transport is None:
        from .jquants.transport import HttpxTransport

        transport = HttpxTransport()
    queue = RequestQueue(transport, TokenBucket(rate_per_minute=50, capacity=5, clock=clock), clock=clock)
    return JQuantsClient(settings.api_key, queue, settings.jquants_base_url)


def fred_queue(transport: Transport, clock: Clock) -> RequestQueue:
    """FRED gets its own queue: 50/min with capacity 1 = one request per
    1.2 s, which enforces the ≥1 s gap between series (要件 4.5)."""
    return RequestQueue(transport, TokenBucket(rate_per_minute=50, capacity=1, clock=clock), clock=clock)


def build_fred_client(settings: Settings, transport: Transport | None = None, clock: Clock | None = None):
    from .fred import FredClient

    clock = clock or SystemClock()
    if transport is None:
        from .jquants.transport import HttpxTransport

        transport = HttpxTransport(text_limit=None)  # whole CSV body
    return FredClient(fred_queue(transport, clock), settings.fred_base_url)
