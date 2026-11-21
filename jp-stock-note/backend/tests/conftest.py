from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from stockapp.config import Settings  # noqa: E402
from stockapp.db import connect, migrate  # noqa: E402
from stockapp.ingest import store_stock_master, store_trading_calendar  # noqa: E402
from stockapp.jquants.transport import HttpRequest, HttpResponse  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TODAY = "2026-11-21"  # fixed "today" (JST) used by query tests


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeTransport:
    """Records every request; replies from a scripted list or a handler."""

    def __init__(self, clock=None, responses=None, handler=None):
        self.clock = clock
        self.responses = list(responses or [])
        self.handler = handler
        self.requests: list[HttpRequest] = []
        self.sent_at: list[float] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if self.clock is not None:
            self.sent_at.append(self.clock.monotonic())
        if self.handler is not None:
            return self.handler(request)
        if not self.responses:
            return HttpResponse(200, {"data": []})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fake_transport_cls():
    return FakeTransport


@pytest.fixture
def db_conn(tmp_path):
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    yield conn
    conn.close()


DAILY_BARS = [
    # code, date, close, adj_close
    ("72030", "2026-11-18", 3010.0, 3010.0),
    ("72030", "2026-11-19", 3030.0, 3030.0),
    ("72030", "2026-11-20", 3051.0, 3051.0),
    ("72670", "2026-11-20", 1500.0, 1500.0),   # only one bar -> change not computable
    ("99840", "2026-11-19", 20000.0, 5000.0),  # split-adjusted: raw differs from adj
    ("99840", "2026-11-20", 20400.0, 5100.0),
]


def seed(conn, *, bars=True, calendar=True):
    with conn:
        store_stock_master(conn, load_fixture("master.json")["data"], "2026-11-21T00:00:00+00:00")
        if calendar:
            store_trading_calendar(
                conn,
                [
                    {"Date": "2026-11-19", "HolDiv": "1"},
                    {"Date": "2026-11-20", "HolDiv": "1"},
                    {"Date": "2026-11-21", "HolDiv": "0"},
                    {"Date": "2026-11-22", "HolDiv": "0"},
                    {"Date": "2026-11-23", "HolDiv": "0"},
                    {"Date": "2026-11-24", "HolDiv": "1"},
                ],
                "2026-11-21T00:00:00+00:00",
            )
        if bars:
            conn.executemany(
                "INSERT INTO daily_bars(code, date, close, adj_close, adj_factor, fetched_at)"
                " VALUES (?, ?, ?, ?, 1.0, '2026-11-21T00:00:00+00:00')",
                DAILY_BARS,
            )


@pytest.fixture
def seeded_conn(db_conn):
    seed(db_conn)
    return db_conn


TEST_KEY = "test-key-DO-NOT-LEAK-0123456789"


def make_jq_client(transport, clock=None, key=TEST_KEY):
    """JQuantsClient over a fake transport, a fake clock and the real queue."""
    from stockapp.jquants.client import JQuantsClient
    from stockapp.jquants.ratelimit import RequestQueue, TokenBucket
    from stockapp.jquants.transport import FakeClock

    clock = clock or FakeClock()
    queue = RequestQueue(transport, TokenBucket(clock=clock), clock=clock, backoff_base_seconds=1.0)
    return JQuantsClient(key, queue, "https://jq.example.invalid")


@pytest.fixture(scope="session")
def synthetic() -> dict:
    """tests/fixtures/synthetic_bars.json (see make_synthetic.py)."""
    return load_fixture("synthetic_bars.json")


def store_synthetic(conn, data: dict) -> None:
    """All synthetic stocks (already on the current adjustment basis) + TOPIX."""
    from stockapp.bars import store_daily_bars, store_topix_bars

    rows = [r for rs in data["stocks"].values() for r in rs]
    rows += data["split_stock"]["bars"] + data["cross_stock"] + data["short_stock"]
    with conn:
        store_daily_bars(conn, rows, "2026-11-21T00:00:00+00:00")
        store_topix_bars(conn, data["topix"], "2026-11-21T00:00:00+00:00")


@pytest.fixture
def synthetic_conn(db_conn, synthetic):
    store_synthetic(db_conn, synthetic)
    return db_conn


@pytest.fixture
def make_settings(tmp_path):
    def _make(**kw) -> Settings:
        s = Settings(
            db_path=tmp_path / "app.db",
            frontend_dist=tmp_path / "dist",
            _api_key=kw.pop("api_key", None),
        )
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    return _make


@pytest.fixture
def make_client(make_settings):
    """TestClient that looks like a local browser (production allow-lists)."""
    from fastapi.testclient import TestClient

    from stockapp.main import create_app

    def _make(settings=None, jq_client=None, peer=("127.0.0.1", 50000), base_url="http://127.0.0.1:8000"):
        app = create_app(settings or make_settings(), client=jq_client)
        return TestClient(app, base_url=base_url, client=peer)

    return _make
