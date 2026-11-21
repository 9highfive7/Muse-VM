"""HTTP transport abstraction.

Every byte that leaves the process for J-Quants goes through a ``Transport``.
Production uses ``HttpxTransport``; tests inject a fake that never touches the
network.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    params: Mapping[str, str] = field(default_factory=dict)
    # Headers are excluded from repr so a stray log line cannot leak the key.
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)


@dataclass
class HttpResponse:
    status: int
    json_body: Any = None
    text: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)


class TransportTimeout(Exception):
    """The request did not complete within the timeout."""


class TransportError(Exception):
    """Connection-level failure (DNS, refused, TLS...)."""


class Transport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse: ...


class HttpxTransport:
    """``text_limit`` caps ``HttpResponse.text`` (J-Quants only needs a short
    error excerpt; the FRED client reads whole CSV bodies, so it passes None)."""

    def __init__(self, timeout_seconds: float = 30.0, text_limit: int | None = 500):
        import httpx

        self._httpx = httpx
        self._timeout = timeout_seconds
        self._text_limit = text_limit
        self._client = None  # created lazily on first send

    def _get_client(self):
        if self._client is None:
            httpx = self._httpx
            # Redirects stay disabled on authenticated requests.
            try:
                self._client = httpx.Client(timeout=self._timeout, follow_redirects=False)
            except httpx.InvalidURL:
                # Some environments put bracketed IPv6 entries such as "[::1]"
                # in NO_PROXY, which httpx cannot parse. Fall back to ignoring
                # the env, but keep an explicit HTTPS proxy if one is set.
                proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or None
                self._client = httpx.Client(
                    timeout=self._timeout, follow_redirects=False, trust_env=False, proxy=proxy
                )
        return self._client

    def send(self, request: HttpRequest) -> HttpResponse:
        httpx = self._httpx
        try:
            resp = self._get_client().request(
                request.method, request.url, params=dict(request.params), headers=dict(request.headers)
            )
        except httpx.TimeoutException as exc:
            raise TransportTimeout(type(exc).__name__) from None
        except httpx.HTTPError as exc:
            raise TransportError(type(exc).__name__) from None
        ctype = resp.headers.get("content-type", "")
        body = None
        if "csv" not in ctype and "zip" not in ctype:  # J-Quants: always JSON (as before)
            try:
                body = resp.json()
            except ValueError:
                body = None
        if ctype.startswith("application/zip") or resp.content[:2] == b"PK":
            text = "PK"  # binary archive: keep only the signature (FRED multi-id requests)
        else:
            text = resp.text if self._text_limit is None else resp.text[: self._text_limit]
        return HttpResponse(status=resp.status_code, json_body=body, text=text, headers=dict(resp.headers))

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def time(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def time(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FakeClock:
    """Deterministic clock for tests: sleep() advances time instantly."""

    def __init__(self, start: float = 0.0, epoch: float = 1_795_000_000.0):
        self.now = start
        self._epoch = epoch
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self._epoch + self.now

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self.sleeps.append(seconds)
            self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds
