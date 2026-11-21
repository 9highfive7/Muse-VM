"""受け入れ条件⑤: 価格位置カードと解説の全文言が禁止語テストを通過する（要件 6.1・6.3）。

Scans content/templates.json, content/glossary.json, every sentence the
server generates for the synthetic stocks (cards, stage facts, notes, cross
history, glossary with substituted values) and the frontend's static text.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from stockapp import technicals, wording
from stockapp.wording import BANNED_WORDS, BannedWordError, find_banned

FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"

# 要件 6.1 左列（禁止する文言・要素）を表のとおりに列挙
REQUIRED_BANNED = [
    "買いシグナル", "売りシグナル", "買い時",
    "割安", "割高", "お買い得",
    "買われすぎ", "売られすぎ",
    "上昇トレンド入り", "底打ち", "天井",
    "目標株価", "理論株価", "適正株価", "上値余地",
    "総合スコア", "★", "おすすめ",
    "好決算", "悪い決算", "サプライズ",
    "業績好調", "財務は安全", "倒産リスク",
    "高配当でお得", "減配の心配",
    "追い風", "不利",
    "すべき", "今がチャンス", "待ちましょう",
    "上がる可能性", "下落が予想",
    "危険", "警告", "要注意",
    "勝率", "的中",
]
STOCK_CODES = ("90010", "90020", "90030", "90040", "90050", "90060", "12340")


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


def assert_clean(texts, where: str) -> int:
    n = 0
    for t in texts:
        n += 1
        bad = find_banned(t)
        assert not bad, f"{where}: banned {bad} in {t!r}"
    return n


def test_banned_list_covers_requirement_6_1():
    missing = [w for w in REQUIRED_BANNED if w not in BANNED_WORDS]
    assert not missing


def test_find_banned_detects_each_word():
    for w in REQUIRED_BANNED:
        assert w in find_banned(f"テスト文{w}を含む")
    assert find_banned("25日移動平均線が75日移動平均線を上抜け（2024-01-26）") == []


def test_templates_file_is_clean():
    raw = json.loads(wording.TEMPLATES_PATH.read_text(encoding="utf-8"))
    assert assert_clean(strings(raw), "templates.json") > 50


def test_glossary_file_is_clean():
    raw = json.loads(wording.GLOSSARY_PATH.read_text(encoding="utf-8"))
    assert assert_clean(strings(raw), "glossary.json") > 50
    for term, entry in wording.glossary().items():   # 3段解説がそろっている
        for key in ("label", "definition", "formula", "formula_generic", "misconception"):
            assert entry.get(key), (term, key)


def test_render_guard_rejects_banned_values():
    assert wording.render("order.mixed") == "混在"      # clean renders pass through
    with pytest.raises(BannedWordError):
        wording.render("s2.rsi", rsi="72.0", zone="買われすぎ", as_of="2024-01-01")


@pytest.mark.parametrize("code", STOCK_CODES)
def test_generated_sentences_are_clean(synthetic_conn, code):
    texts = []
    card = technicals.price_position_card(synthetic_conn, code)
    texts += list(strings(card))
    for rng in technicals.RANGES:
        texts += list(strings(technicals.technicals_payload(synthetic_conn, code, rng)))
    chart = technicals.chart_payload(synthetic_conn, code, "5y")
    texts += chart["notes"] + [m["label"] for m in chart["ma_styles"]]
    for term in wording.glossary():
        texts += list(strings(technicals.glossary_entry(synthetic_conn, term, code)))
    assert assert_clean(texts, code) > 20


def test_card_sentences_max_four_and_dated(synthetic_conn):
    for code in STOCK_CODES[:-1]:
        card = technicals.price_position_card(synthetic_conn, code)
        assert 1 <= len(card["sentences"]) <= 4
        for s in card["sentences"]:
            assert "基準日：" in s["text"]


def test_frontend_static_text_is_clean():
    files = sorted(FRONTEND_SRC.rglob("*.ts")) + sorted(FRONTEND_SRC.rglob("*.tsx"))
    assert files
    for f in files:
        text = f.read_text(encoding="utf-8")
        bad = find_banned(text)
        assert not bad, f"{f.name}: banned {bad}"


# --------------------------------------------------------------------------- Phase 4 (受け入れ条件⑥: 全画面の文言)

def test_every_content_file_is_clean():
    """All server-side wording files, incl. Phase 4 memo_form.json (S4),
    watch_rules.json (ウォッチリスト) and about.json (S0 初回説明・拡張版免責)."""
    files = sorted(wording.CONTENT_DIR.glob("*.json"))
    names = {f.name for f in files}
    assert {"memo_form.json", "watch_rules.json", "about.json", "templates.json"} <= names
    for f in files:
        assert_clean(strings(json.loads(f.read_text(encoding="utf-8"))), f.name)


def test_phase4_generated_sentences_are_clean(make_client, make_settings):
    """Digest, watchlist, memo review payload and export as the screens receive them."""
    from phase4_helpers import add_master_codes, payload, seeded_api

    client, conn = seeded_api(make_client, make_settings)
    try:
        add_master_codes(conn, ["90010"])
        assert client.post("/api/watchlist", json={"code": "7203", "reason": "決算の方向を観察",
                                                    "tags": ["決算待ち"]}).status_code == 200
        memo = client.post("/api/stocks/7203/memos", json=payload(review_date="2026-01-01")).json()
        texts = []
        for path in ("/api/digest", "/api/watchlist", f"/api/memos/{memo['memo_id']}/review",
                     "/api/memos", "/api/about", "/api/memo-form", "/api/memos/verify"):
            r = client.get(path)
            assert r.status_code == 200, path
            texts += list(strings(r.json()))
        texts.append(client.get("/api/export?format=md").text)
        assert assert_clean(texts, "phase4 payloads") > 30
    finally:
        conn.close()
