"""Search: 全角半角・大文字小文字の正規化 and partial matching (要件 3.1, S2)."""
from __future__ import annotations

import pytest

from conftest import TODAY
from stockapp.normalize import normalize_text
from stockapp.queries import search_stocks


def codes(conn, q):
    return [r["code"] for r in search_stocks(conn, q, today=TODAY)["results"]]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ＫＤＤＩ", "kddi"),
        ("ｋｄｄｉ", "kddi"),
        ("７２０３", "7203"),
        ("ﾄﾖﾀ", "トヨタ"),          # half-width kana -> full-width
        ("ＭＳ＆ＡＤ", "ms&ad"),
        ("Ｖｅｒｉｔａｓ　Ｉｎ", "veritasin"),  # ideographic space dropped
        ("  soft  bank ", "softbank"),
    ],
)
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


def test_code_partial_match(seeded_conn):
    assert codes(seeded_conn, "7203") == ["72030"]
    assert codes(seeded_conn, "720") == ["72030"]
    # partial match anywhere in the code: 87250 contains "72" too
    assert set(codes(seeded_conn, "72")) == {"72030", "72670", "87250"}


def test_full_width_code_matches(seeded_conn):
    assert codes(seeded_conn, "７２０３") == ["72030"]


def test_alphanumeric_code_case_insensitive(seeded_conn):
    assert codes(seeded_conn, "130a") == ["130A0"]
    assert codes(seeded_conn, "１３０Ａ") == ["130A0"]


def test_name_match_full_width_stored_half_width_query(seeded_conn):
    # Master stores "ＫＤＤＩ" (full-width); user types lower-case ASCII.
    assert codes(seeded_conn, "kddi") == ["94330"]
    assert codes(seeded_conn, "KdDi") == ["94330"]


def test_name_match_half_width_kana(seeded_conn):
    assert codes(seeded_conn, "ﾄﾖﾀ") == ["72030"]
    assert codes(seeded_conn, "ソフトバンク") == ["99840"]


def test_name_partial_middle_of_name(seeded_conn):
    assert codes(seeded_conn, "インシュアランス") == ["87250"]
    assert codes(seeded_conn, "ms&ad") == ["87250"]


def test_english_name_match(seeded_conn):
    assert codes(seeded_conn, "honda") == ["72670"]


def test_like_wildcards_are_literal(seeded_conn):
    assert codes(seeded_conn, "%") == ["20800"]      # only the name containing a literal %
    assert codes(seeded_conn, "_") == ["20800"]      # only the English name with a literal _
    assert codes(seeded_conn, "zzz") == []


def test_empty_query_returns_nothing(seeded_conn):
    assert search_stocks(seeded_conn, "   ", today=TODAY)["results"] == []


def test_latest_effective_master_row_is_used(seeded_conn):
    # 72030 has rows for 2026-11-20 and a future-dated 2026-11-24; today is 11-21.
    rows = search_stocks(seeded_conn, "7203", today=TODAY)["results"]
    assert rows[0]["name"] == "トヨタ自動車"
    assert rows[0]["master_date"] == "2026-11-20"
    later = search_stocks(seeded_conn, "7203", today="2026-11-25")["results"]
    assert later[0]["master_date"] == "2026-11-24"


def test_results_include_market_and_sectors(seeded_conn):
    r = search_stocks(seeded_conn, "トヨタ", today=TODAY)["results"][0]
    assert r["display_code"] == "7203"
    assert r["market"] == {"code": "0111", "name": "プライム"}
    assert r["sector17"] == {"code": "6", "name": "自動車・輸送機"}
    assert r["sector33"] == {"code": "3700", "name": "輸送用機器"}


def test_exact_code_ranks_first(seeded_conn):
    # "9984" matches code 99840 exactly (4-digit form)
    assert codes(seeded_conn, "9984")[0] == "99840"


def test_search_endpoint(make_settings, make_client):
    from stockapp.db import connect
    from conftest import seed

    settings = make_settings()
    client = make_client(settings)
    conn = connect(settings.db_path)
    seed(conn)
    conn.close()
    res = client.get("/api/search", params={"q": "ｔｏｙｏｔａ"})
    assert res.status_code == 200
    body = res.json()
    assert [r["code"] for r in body["results"]] == ["72030"]
    assert body["results"][0]["sector33"]["name"] == "輸送用機器"
