"""Phase 4 判断メモ (要件 3.10 / 7.3, 受け入れ条件①②③):
snapshot immutability (triggers + SHA-256 verification), revisions,
snapshot-vs-current review payload, self-review annotation, validation."""
from __future__ import annotations

import json
import sqlite3

import pytest

from phase4_helpers import payload, seeded_api
from stockapp import memos
from stockapp.db import connect


@pytest.fixture
def api(make_client, make_settings):
    client, conn = seeded_api(make_client, make_settings)
    yield client, conn
    conn.close()


def _create(client, code="7203", **over):
    r = client.post(f"/api/stocks/{code}/memos", json=payload(**over))
    assert r.status_code == 200, r.text
    return r.json()


def _field(snapshot_values: dict, key: str) -> dict:
    return next(f for f in snapshot_values["fields"] if f["key"] == key)


def _add_bar(conn, day: str, close: float):
    with conn:
        conn.execute("INSERT INTO daily_bars(code, date, close, adj_close, adj_factor, fetched_at)"
                     " VALUES ('72030', ?, ?, ?, 1.0, '2026-11-24T12:00:00+00:00')", (day, close, close))


# --------------------------------------------------------------------------- immutability

@pytest.mark.parametrize("table", ["memo", "memo_revision", "memo_snapshot", "memo_review"])
def test_update_and_delete_are_rejected_by_triggers(api, table):
    client, conn = api
    m = _create(client)
    client.post(f"/api/memos/{m['memo_id']}/reviews", json={"process": "followed", "result": "unclear"})
    col = {"memo": "code", "memo_revision": "content_json", "memo_snapshot": "values_json",
           "memo_review": "content_json"}[table]
    with pytest.raises(sqlite3.DatabaseError, match="not allowed"):
        with conn:
            conn.execute(f"UPDATE {table} SET {col} = 'x'")
    with pytest.raises(sqlite3.DatabaseError, match="not allowed"):
        with conn:
            conn.execute(f"DELETE FROM {table}")
    assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
    assert memos.verify_memo(conn, m["memo_id"])["ok"]


def test_snapshot_trigger_rejects_even_a_no_op_update(api):
    client, conn = api
    _create(client)
    with pytest.raises(sqlite3.DatabaseError, match="memo_snapshot is immutable"):
        with conn:
            conn.execute("UPDATE memo_snapshot SET sha256 = sha256")


def _trigger_free_copy(conn, tmp_path) -> sqlite3.Connection:
    """Byte-level copy of the DB whose immutability triggers are dropped —
    simulates editing the file outside the app."""
    dest = tmp_path / "tampered.db"
    target = sqlite3.connect(dest)
    conn.backup(target)
    target.close()
    copy = connect(dest)
    for name in ("memo_snapshot_no_update", "memo_snapshot_no_delete", "memo_revision_no_update",
                 "memo_review_no_update"):
        copy.execute(f"DROP TRIGGER {name}")
    return copy


def test_hash_verification_passes_untouched_and_fails_after_forced_change(api, tmp_path):
    client, conn = api
    m = _create(client)
    snap_id = m["latest"]["snapshot"]["snapshot_id"]
    assert memos.verify_memo(conn, m["memo_id"]) == {"ok": True, "checked": 2, "problems": [],
                                                     "text": "改変なし（保存時のハッシュと一致）"}
    assert memos.verify_snapshot(conn, snap_id)
    assert client.get(f"/api/memos/{m['memo_id']}").json()["verification"]["ok"] is True

    copy = _trigger_free_copy(conn, tmp_path)
    try:
        with copy:   # forced change: the snapshot's close 3051 -> 9999
            n = copy.execute("UPDATE memo_snapshot SET values_json = replace(values_json, '\"value\":3051.0',"
                             " '\"value\":9999.0')").rowcount
        assert n == 1
        assert json.loads(copy.execute("SELECT values_json FROM memo_snapshot").fetchone()[0])["fields"][0]["value"] \
            == 9999.0
        v = memos.verify_memo(copy, m["memo_id"])
        assert v["ok"] is False
        assert [p["table"] for p in v["problems"]] == ["memo_snapshot"]
        assert not memos.verify_snapshot(copy, snap_id)
        assert memos.verify_all(copy)["tampered_memo_ids"] == [m["memo_id"]]
        # re-hashing only the values is not enough either: the stored sha256 no longer matches
        with copy:
            copy.execute("UPDATE memo_revision SET content_json = replace(content_json, 'watch', 'hold')")
        assert {p["table"] for p in memos.verify_memo(copy, m["memo_id"])["problems"]} == {"memo_snapshot",
                                                                                          "memo_revision"}
    finally:
        copy.close()
    # the original DB is untouched
    assert memos.verify_memo(conn, m["memo_id"])["ok"]


def test_verification_reports_a_deleted_snapshot(api, tmp_path):
    client, conn = api
    m = _create(client)
    copy = _trigger_free_copy(conn, tmp_path)
    try:
        with copy:
            copy.execute("DELETE FROM memo_snapshot")
        v = memos.verify_memo(copy, m["memo_id"])
        assert not v["ok"] and "スナップショットがない" in v["problems"][0]["reason"]
    finally:
        copy.close()


# --------------------------------------------------------------------------- revisions & snapshot binding

def test_edit_creates_second_revision_and_first_snapshot_stays_bound(api):
    client, conn = api
    m = _create(client)
    first = m["latest"]
    assert first["revision_no"] == 1
    assert _field(first["snapshot"]["values"], "close")["value"] == 3051.0

    _add_bar(conn, "2026-11-24", 3100.0)          # market data changes after the first save
    edited = payload(stance="hold", scenario={"expected": "書き直した想定", "different": "書き直した別の想定"})
    r = client.post(f"/api/memos/{m['memo_id']}/revisions", json=edited)
    assert r.status_code == 200, r.text
    d = r.json()
    assert [x["revision_no"] for x in d["revisions"]] == [1, 2]
    r1, r2 = d["revisions"]
    # revision 1 and its snapshot are exactly as first saved
    assert r1["content"] == first["content"] and r1["content"]["stance"] == "watch"
    assert r1["snapshot"] == first["snapshot"]
    assert _field(r1["snapshot"]["values"], "close") == _field(first["snapshot"]["values"], "close")
    assert _field(r1["snapshot"]["values"], "close")["as_of"] == "2026-11-20"
    # revision 2 carries the new content and the values at its own save time
    assert r2["content"]["stance"] == "hold"
    assert _field(r2["snapshot"]["values"], "close")["value"] == 3100.0
    assert _field(r2["snapshot"]["values"], "close")["as_of"] == "2026-11-24"
    assert d["verification"]["ok"] and d["verification"]["checked"] == 4
    # old revisions stay readable
    again = client.get(f"/api/memos/{m['memo_id']}").json()
    assert again["revisions"][0]["content"]["scenario"] == first["content"]["scenario"]


def test_core_five_snapshot_fields_have_as_of_and_unavailable_reason(api):
    client, conn = api
    m = _create(client)
    vals = m["latest"]["snapshot"]["values"]
    core = {f["key"]: f for f in vals["fields"] if f["core"]}
    assert set(core) == set(memos.CORE_KEYS)
    for f in core.values():
        assert "as_of" in f and "reason" in f
        if f["available"]:
            assert f["as_of"] and f["reason"] is None
        else:
            assert f["value"] is None and f["reason"]       # 算出不可 is recorded with its reason
    assert core["close"]["available"] and core["close"]["as_of"] == "2026-11-20"
    assert not core["per"]["available"]                       # no financial summaries in this fixture
    # stage facts and card sentences are part of the snapshot
    keys = {t["key"] for t in vals["texts"]}
    assert {"card.price_position", "stage1", "stage2", "stage3", "stage4", "alerts", "card.performance",
            "card.health", "card.valuation", "macro"} <= keys


def test_list_endpoints(api):
    client, conn = api
    m = _create(client)
    lst = client.get("/api/stocks/7203/memos").json()
    assert lst["count"] == 1 and lst["items"][0]["memo_id"] == m["memo_id"]
    assert lst["items"][0]["stance_label"] == "監視"
    assert client.get("/api/stocks/6758/memos").json()["count"] == 0
    recent = client.get("/api/memos").json()
    assert recent["items"][0]["display_code"] == "7203" and recent["items"][0]["name"] == "トヨタ自動車"
    assert client.get("/api/memos/999").status_code == 404


# --------------------------------------------------------------------------- review comparison

def test_review_payload_pairs_snapshot_and_current_for_the_same_keys(api):
    client, conn = api
    m = _create(client)
    _add_bar(conn, "2026-11-24", 3100.0)
    r = client.get(f"/api/memos/{m['memo_id']}/review")
    assert r.status_code == 200
    d = r.json()
    snap_keys = [f["key"] for f in m["latest"]["snapshot"]["values"]["fields"]]
    assert [row["key"] for row in d["rows"]] == snap_keys
    for row in d["rows"]:
        assert set(row["snapshot"]) == set(row["current"]) == {"value", "available", "as_of", "reason"}
    close = next(row for row in d["rows"] if row["key"] == "close")
    assert close["snapshot"]["value"] == 3051.0 and close["snapshot"]["as_of"] == "2026-11-20"
    assert close["current"]["value"] == 3100.0 and close["current"]["as_of"] == "2026-11-24"
    assert close["diff"] == 49.0
    per = next(row for row in d["rows"] if row["key"] == "per")
    assert per["diff"] is None                     # unavailable on both sides -> no diff
    assert [t["key"] for t in d["texts"]] == [t["key"] for t in m["latest"]["snapshot"]["values"]["texts"]]
    assert all(isinstance(t["snapshot"], list) and isinstance(t["current"], list) for t in d["texts"])
    # reviewing never writes: snapshot still verifies and is unchanged
    assert client.get(f"/api/memos/{m['memo_id']}").json()["latest"]["snapshot"] == m["latest"]["snapshot"]
    assert client.get(f"/api/memos/{m['memo_id']}/review?revision_id=999").status_code == 404


def test_self_review_is_a_later_annotation_and_never_overwrites(api):
    client, conn = api
    m = _create(client, review_date="2026-11-01")
    assert client.get("/api/memos").json()["items"][0]["due"] is True
    before = m["latest"]
    r = client.post(f"/api/memos/{m['memo_id']}/reviews",
                    json={"process": "partly", "result": "different", "note": "決算前に判断を変えた"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["reviews"][0]["process"] == "partly" and d["reviews"][0]["result"] == "different"
    assert d["reviews"][0]["revision_id"] == before["revision_id"]
    assert d["latest"] == before and len(d["revisions"]) == 1   # snapshot/revision untouched
    assert d["verification"]["ok"] and d["verification"]["checked"] == 3
    assert client.get("/api/memos").json()["items"][0]["due"] is False
    rv = client.get(f"/api/memos/{m['memo_id']}/review").json()
    assert rv["reviews"][0]["note"] == "決算前に判断を変えた"
    for bad in ({"process": "great", "result": "different"}, {"process": "partly"}, {"result": "unclear"}):
        assert client.post(f"/api/memos/{m['memo_id']}/reviews", json=bad).status_code == 422


# --------------------------------------------------------------------------- validation / no suggestion

@pytest.mark.parametrize("over,needle", [
    ({"stance": None}, "区分"),
    ({"stance": "buy"}, "区分"),
    ({"evidence": [{"area": "chart", "item": "stage1", "note": "a"}, {"area": "financial", "item": "alerts", "note": "b"}]},
     "市場環境"),
    ({"evidence": [{"area": "chart", "item": "stage1", "note": ""}, {"area": "financial", "item": "alerts", "note": "b"},
                   {"area": "macro", "item": "macro.topix", "note": "c"}]}, "所見"),
    ({"evidence": [{"area": "chart", "item": "made-up", "note": "a"}]}, "選択肢"),
    ({"scenario": {"expected": "x", "different": " "}}, "想定と異なる場合"),
    ({"conditions": []}, "見直し条件"),
    ({"conditions": [{"kind": "other", "text": "  "}]}, "見直し条件"),
    ({"review_date": "2026/12/01"}, "見直し日"),
])
def test_incomplete_memo_is_rejected(api, over, needle):
    client, conn = api
    r = client.post("/api/stocks/7203/memos", json=payload(**over))
    assert r.status_code == 422
    assert needle in r.json()["detail"]
    assert conn.execute("SELECT COUNT(*) FROM memo").fetchone()[0] == 0


def test_unknown_stock_is_404(api):
    client, _ = api
    assert client.post("/api/stocks/1111/memos", json=payload()).status_code == 404


def test_form_never_preselects_or_suggests_a_stance(api):
    client, _ = api
    f = client.get("/api/memo-form").json()
    assert [o["label"] for o in f["stance"]["options"]] == ["監視", "保有", "見送り"]
    dumped = json.dumps(f, ensure_ascii=False)
    for word in ('"default"', '"selected"', '"suggested"', '"recommended"', '"checked"'):
        assert word not in dumped
    assert f["notice"] == "このメモは自分の判断過程の記録であり、アプリは内容を評価しません。"
    # the stored stance is exactly what the user sent, for each option
    for key in ("watch", "hold", "pass"):
        m = client.post("/api/stocks/7203/memos", json=payload(stance=key)).json()
        assert m["latest"]["content"]["stance"] == key


def test_export_json_and_markdown(api):
    client, _ = api
    _create(client)
    client.post("/api/watchlist", json={"code": "7203", "reason": "決算の方向を確認", "tags": ["決算待ち"]})
    j = client.get("/api/export?format=json")
    assert j.status_code == 200 and "attachment" in j.headers["content-disposition"]
    data = j.json()
    assert len(data["memos"]) == 1 and data["watchlist"][0]["reason"] == "決算の方向を確認"
    assert data["verification"]["ok"]
    md = client.get("/api/export?format=md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "区分（自分の記録）：監視" in md.text and "SHA-256" in md.text and "終値（調整後）" in md.text
