"""受け入れ条件①: 株式分割のあった銘柄で、分割日前後のローソク足と移動平均に段差がない。

Flow under test (要件 4.2-5 / 4.4): the code's history is stored before the
split; the nightly by-date fetch then delivers the split day with
AdjFactor != 1, which queues a P3 full re-fetch by code; after the re-fetch the
whole adjusted series is on the new basis.
"""
from __future__ import annotations

from conftest import make_jq_client
from stockapp import bars, technicals
from stockapp.jquants.ratelimit import Priority
from stockapp.jquants.transport import HttpResponse

TS = "2026-11-21T00:00:00+00:00"


def pre_split_view(rows: list[dict], split_idx: int) -> list[dict]:
    """What the API returned before the split happened: adjusted == raw."""
    out = []
    for r in rows[:split_idx]:
        out.append({**r, "AdjFactor": 1.0, "AdjO": r["O"], "AdjH": r["H"], "AdjL": r["L"], "AdjC": r["C"],
                    "AdjVo": r["Vo"]})
    return out


def handler_for(split_rows: list[dict], api_first_idx: int = 0):
    def handle(req):
        p = req.params
        if "code" in p:
            return HttpResponse(200, {"data": split_rows[api_first_idx:]})
        if "date" in p:
            return HttpResponse(200, {"data": [r for r in split_rows if r["Date"] == p["date"]]})
        return HttpResponse(200, {"data": []})
    return handle


def max_abs_daily_change(values: list[float | None]) -> float:
    vals = [v for v in values if v is not None]
    return max(abs(b / a - 1) for a, b in zip(vals, vals[1:]))


def test_split_detected_and_series_continuous(db_conn, synthetic, fake_transport_cls):
    split = synthetic["split_stock"]
    rows, code, split_date = split["bars"], split["code"], split["split_date"]
    split_idx = next(i for i, r in enumerate(rows) if r["Date"] == split_date)
    assert rows[split_idx]["AdjFactor"] == 0.5

    with db_conn:
        bars.store_daily_bars(db_conn, pre_split_view(rows, split_idx), TS)

    transport = fake_transport_cls(handler=handler_for(rows))
    client = make_jq_client(transport)

    # nightly by-date fetch of the split day
    out = bars.fetch_bars_by_date(db_conn, client, split_date)
    assert out["result"] == "ok" and out["split_refetch_queued"] == [code]
    job = db_conn.execute("SELECT * FROM fetch_queue WHERE status='pending'").fetchone()
    assert (job["job"], job["code"], job["priority"], job["reason"]) == \
        ("bars_full", code, Priority.P3_REFETCH, "split_detected")
    ev = db_conn.execute("SELECT * FROM adjustment_events").fetchone()
    assert (ev["code"], ev["date"], ev["adj_factor"], ev["action"]) == (code, split_date, 0.5, "refetch_queued")

    # Before the re-fetch the stored adjusted series has a ~-50% step — this is
    # what the re-fetch exists to remove (and proves the check below can fail).
    stale = technicals.chart_payload(db_conn, code, "5y")
    i = stale["dates"].index(split_date)
    assert stale["candles"][i]["close"] / stale["candles"][i - 1]["close"] < 0.6

    done = bars.run_queue(db_conn, client)
    assert [d["result"] for d in done] == ["ok"]
    by_code = [r for r in transport.requests if "code" in r.params]
    assert len(by_code) == 1 and by_code[0].params == {"code": code}

    chart = technicals.chart_payload(db_conn, code, "5y")
    dates = chart["dates"]
    i = dates.index(split_date)
    closes = [c["close"] for c in chart["candles"]]
    # stored raw closes do jump at the split ...
    raw = dict(db_conn.execute("SELECT date, close FROM daily_bars WHERE code=?", (code,)).fetchall())
    assert raw[dates[i]] / raw[dates[i - 1]] < 0.6
    # ... adjusted candles do not (synthetic daily σ is 1.1%)
    for key in ("open", "high", "low", "close"):
        assert abs(chart["candles"][i][key] / chart["candles"][i - 1]["close"] - 1) < 0.05, key
    assert max_abs_daily_change(closes) < 0.06
    # adjusted volume is on the post-split share basis too (no 2x step)
    vols = [c["volume"] for c in chart["candles"]]
    assert 0.25 < (sum(vols[i:i + 20]) / 20) / (sum(vols[i - 20:i]) / 20) < 4
    # moving averages: no step at the split date
    for key in ("sma5", "sma25", "sma75", "sma200"):
        series = chart["ma"][key]
        assert series[i] is not None
        assert abs(series[i] / series[i - 1] - 1) < 0.02, key
        assert max_abs_daily_change(series) < 0.03, key
    # and the adjusted series equals the API's current-basis series exactly
    assert closes == [r["AdjC"] for r in rows]


def test_split_refetch_rescales_rows_older_than_api_window(db_conn, synthetic, fake_transport_cls):
    """Rows older than the API's history window are never deleted (要件 4.4);
    after the re-fetch they are rescaled so the series stays continuous."""
    split = synthetic["split_stock"]
    rows, code, split_date = split["bars"], split["code"], split["split_date"]
    split_idx = next(i for i, r in enumerate(rows) if r["Date"] == split_date)
    api_first = 40   # the API "forgot" the first 40 days by the time of the re-fetch

    with db_conn:
        bars.store_daily_bars(db_conn, pre_split_view(rows, split_idx), TS)
    transport = fake_transport_cls(handler=handler_for(rows, api_first_idx=api_first))
    client = make_jq_client(transport)
    bars.fetch_bars_by_date(db_conn, client, split_date)
    bars.run_queue(db_conn, client)

    stored = db_conn.execute(
        "SELECT date, adj_close FROM daily_bars WHERE code=? ORDER BY date", (code,)).fetchall()
    assert len(stored) == len(rows)          # nothing deleted
    for (d, adj), r in zip(stored, rows):
        assert d == r["Date"]
        assert abs(adj - r["AdjC"]) < 0.011, d   # old rows rescaled onto the new basis
    chart = technicals.chart_payload(db_conn, code, "5y")
    assert max_abs_daily_change([c["close"] for c in chart["candles"]]) < 0.06
    assert max_abs_daily_change(chart["ma"]["sma25"]) < 0.03


def test_tail_fetch_with_split_refetches_immediately(db_conn, synthetic, fake_transport_cls):
    """On-demand tail fetch (user waiting): split inside the tail -> full re-fetch now, not queued."""
    split = synthetic["split_stock"]
    rows, code, split_date = split["bars"], split["code"], split["split_date"]
    split_idx = next(i for i, r in enumerate(rows) if r["Date"] == split_date)
    with db_conn:
        bars.store_daily_bars(db_conn, pre_split_view(rows, split_idx), TS)

    def handle(req):
        p = req.params
        if "from" in p:
            return HttpResponse(200, {"data": [r for r in rows if r["Date"] >= p["from"]]})
        return HttpResponse(200, {"data": rows})

    transport = fake_transport_cls(handler=handle)
    out = bars.fetch_bars_since(db_conn, make_jq_client(transport), code, split_date)
    assert out["split_detected"] is True and out["full_refetch"]["result"] == "ok"
    assert db_conn.execute("SELECT COUNT(*) FROM fetch_queue").fetchone()[0] == 0
    chart = technicals.chart_payload(db_conn, code, "5y")
    assert max_abs_daily_change([c["close"] for c in chart["candles"]]) < 0.06
