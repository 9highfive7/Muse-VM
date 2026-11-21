"""Synthetic FRED fredgraph.csv bodies and helpers (Phase 3). Not real data.

The CSV shape matches what fredgraph.csv returned on 2026-11-21:
``observation_date,<ID>`` header, ISO dates, empty field for a missing
observation (legacy files used ``.``; both are exercised).
"""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlsplit

from conftest import FakeTransport
from fins_fixtures import weekdays
from stockapp import fred
from stockapp.jquants.transport import FakeClock, HttpResponse

SERIES = fred.SERIES_IDS


def csv_text(series_id: str, rows: list[tuple[str, object]], header: str = "observation_date") -> str:
    lines = [f"{header},{series_id}"]
    for d, v in rows:
        lines.append(f"{d},{'' if v is None else v}")
    return "\n".join(lines) + "\n"


def csv_response(series_id: str, rows, header: str = "observation_date") -> HttpResponse:
    return HttpResponse(200, None, csv_text(series_id, rows, header), {"content-type": "application/csv"})


def months(start: str, n: int) -> list[str]:
    d = date.fromisoformat(start)
    out = []
    for _ in range(n):
        out.append(d.isoformat())
        d = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
    return out


def linear(dates: list[str], start: float, step: float, digits: int = 4) -> list[tuple[str, float]]:
    return [(d, round(start + step * i, digits)) for i, d in enumerate(dates)]


def daily_dates(end: str = "2026-11-19", n: int = 300) -> list[str]:
    e = date.fromisoformat(end)
    s = e - timedelta(days=int(n * 7 / 5) + 20)
    return weekdays(s.isoformat(), end)[-n:]


def standard_bodies() -> dict[str, list[tuple[str, object]]]:
    """All five series with enough history for the 250-observation windows.

    * DGS10 rises 0.002/day (60 obs -> +0.12 pt), DGS2 rises 0.001/day.
    * DEXJPUS ends 2026-11-13 at 159.93 (a week behind, as in production).
    * JP10y / call rate are monthly, last observation 2026-10-01, rising.
    """
    us = daily_dates("2026-11-19", 300)
    fx = daily_dates("2026-11-13", 300)
    jp = months("2005-11-01", 252)          # ... 2026-10-01
    fx_rows = linear(fx, 159.93 - 0.01 * 299, 0.01, 2)
    fx_rows[-1] = (fx_rows[-1][0], 159.93)
    return {
        "DGS10": linear(us, 3.50, 0.002),
        "DGS2": linear(us, 3.00, 0.001),
        "DEXJPUS": fx_rows,
        "IRLTLT01JPM156N": linear(jp, 0.5, 0.005),
        "IRSTCI01JPM156N": linear(jp, 0.0, 0.004),
    }


class FredHandler:
    """Transport handler: replies per series id; ``fail`` maps id -> status
    (or Exception) to simulate an outage for that series only."""

    def __init__(self, bodies: dict[str, list], fail: dict | None = None, zip_for: set | None = None):
        self.bodies = bodies
        self.fail = fail or {}
        self.zip_for = zip_for or set()

    def __call__(self, request):
        assert urlsplit(request.url).path == "/graph/fredgraph.csv"
        sid = request.params["id"]
        assert "," not in sid, "FRED must be fetched one series at a time"
        if sid in self.fail:
            f = self.fail[sid]
            if isinstance(f, Exception):
                raise f
            return HttpResponse(f, None, "error", {"content-type": "text/html"})
        if sid in self.zip_for:
            return HttpResponse(200, None, "PK", {"content-type": "application/zip"})
        return csv_response(sid, self.bodies[sid])


def make_fred_client(handler, clock: FakeClock | None = None):
    from stockapp.runtime import build_fred_client
    from stockapp.config import Settings

    clock = clock or FakeClock()
    transport = FakeTransport(clock=clock, handler=handler)
    client = build_fred_client(Settings(fred_base_url="https://fred.example.invalid"), transport, clock)
    # faster retries in tests (the queue still retries 5xx three times)
    client.queue.backoff_base = 0.5
    return client, transport, clock


def seed_fred(conn, bodies: dict[str, list] | None = None, today: date = date(2026, 11, 21)) -> dict:
    client, transport, _ = make_fred_client(FredHandler(bodies or standard_bodies()))
    out = fred.fetch_all(conn, client, today=today)
    assert out["result"] == "ok", out
    return out


def seed_topix(conn, end: str = "2026-11-20", n: int = 300, start: float = 2500.0, step: float = 1.0):
    dates = weekdays("2025-01-01", end)[-n:]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO topix_bars(date, open, high, low, close, fetched_at)"
            " VALUES (?, ?, ?, ?, ?, '2026-11-21T00:00:00+00:00')",
            [(d, start + step * i, start + step * i, start + step * i, start + step * i) for i, d in enumerate(dates)],
        )
    return dates
