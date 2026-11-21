"""Shared helpers for the Phase 4 tests (memo / watchlist / S0). Fixtures only."""
from __future__ import annotations

import json

from conftest import seed

MEMO_PAYLOAD = {
    "stance": "watch",
    "evidence": [
        {"area": "chart", "item": "stage1", "note": "移動平均の配列を確認した"},
        {"area": "financial", "item": "alerts", "note": "確認ポイントの該当なしを確認した"},
        {"area": "macro", "item": "macro.topix", "note": "TOPIXの推移を確認した"},
    ],
    "scenario": {"expected": "次回決算で通期予想が据え置かれる", "different": "通期予想が下方修正される"},
    "conditions": [{"kind": "next_earnings", "text": "次回決算開示時に見直す"}],
    "review_date": "2026-12-15",
}


def payload(**over) -> dict:
    p = json.loads(json.dumps(MEMO_PAYLOAD, ensure_ascii=False))
    p.update(over)
    return p


def add_master_codes(conn, codes: list[str]) -> None:
    """Extra synthetic stock-master rows (code -> 'テスト銘柄<code>')."""
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO stock_master(code, date, name, name_norm, sector17_code, sector17_name,"
            " raw_json, fetched_at) VALUES (?, '2026-11-20', ?, ?, '6', '自動車・輸送機', '{}', '2026-11-21T00:00:00+00:00')",
            [(c, f"テスト銘柄{c}", f"テスト銘柄{c}") for c in codes],
        )


def seeded_api(make_client, make_settings):
    """(TestClient, sqlite connection) over one temp DB seeded with conftest.seed."""
    from stockapp.db import connect, migrate

    settings = make_settings()
    conn = connect(settings.db_path)
    migrate(conn)
    seed(conn)
    return make_client(settings), conn
