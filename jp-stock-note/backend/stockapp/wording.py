"""Template rendering for factual sentences (要件 6.3) and the banned-word list
(要件 6.1). All user-facing sentences are produced here from
content/templates.json; the frontend only displays them.
"""
from __future__ import annotations

import json
import math
import string
from functools import lru_cache
from pathlib import Path

CONTENT_DIR = Path(__file__).resolve().parent / "content"
TEMPLATES_PATH = CONTENT_DIR / "templates.json"
GLOSSARY_PATH = CONTENT_DIR / "glossary.json"                       # Phase 1 (technical) terms
FIN_GLOSSARY_PATH = CONTENT_DIR / "glossary_financial.json"         # Phase 2 (financial) terms
MACRO_GLOSSARY_PATH = CONTENT_DIR / "glossary_macro.json"           # Phase 3 (macro) terms

# 要件 6.1 禁止する文言・要素（左列）。Substring match.
BANNED_WORDS = (
    "買いシグナル", "売りシグナル", "買い時", "売り時",
    "割安", "割高", "お買い得",
    "買われすぎ", "売られすぎ", "買われ過ぎ", "売られ過ぎ",
    "上昇トレンド入り", "底打ち", "天井",
    "目標株価", "理論株価", "適正株価", "上値余地",
    "総合スコア", "★", "おすすめ", "お勧め", "推奨",
    "好決算", "悪い決算", "サプライズ",
    "業績好調", "財務は安全", "倒産リスク",
    "高配当でお得", "減配の心配",
    "追い風", "不利",
    "すべき", "今がチャンス", "チャンス", "待ちましょう",
    "上がる可能性", "下落が予想", "上昇が予想", "予想される",
    "危険", "警告", "要注意",
    "勝率", "的中",
)


class BannedWordError(ValueError):
    pass


def find_banned(text: str) -> list[str]:
    return [w for w in BANNED_WORDS if w in text]


@lru_cache(maxsize=1)
def templates() -> dict[str, str]:
    data = json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def glossary() -> dict[str, dict[str, str]]:
    data = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def fin_glossary() -> dict[str, dict[str, str]]:
    data = json.loads(FIN_GLOSSARY_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def macro_glossary() -> dict[str, dict[str, str]]:
    data = json.loads(MACRO_GLOSSARY_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def all_glossary() -> dict[str, dict[str, str]]:
    return {**glossary(), **fin_glossary(), **macro_glossary()}


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def render(key: str, **values) -> str:
    text = templates()[key].format(**values)
    bad = find_banned(text)
    if bad:  # runtime guard in addition to the test
        raise BannedWordError(f"banned words {bad} in rendered template {key!r}")
    return text


# --------------------------------------------------------------------------- number formatting

MINUS = "−"  # U+2212, as in the requirement examples


def _bad(v) -> bool:
    return v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))


def signed(v: float | None, digits: int = 1) -> str:
    """+3.2 / −2.1 / 0.0"""
    if _bad(v):
        return "—"
    r = round(float(v), digits)
    if r == 0:
        return f"{0:.{digits}f}"
    return f"+{r:,.{digits}f}" if r > 0 else f"{MINUS}{abs(r):,.{digits}f}"


def num(v: float | None, digits: int = 1) -> str:
    if _bad(v):
        return "—"
    r = round(float(v), digits)
    if r == 0:
        r = 0.0
    s = f"{abs(r):,.{digits}f}"
    return f"{MINUS}{s}" if r < 0 else s


def yen(v: float | None) -> str:
    """Amounts in yen with 兆/億/万 units: 1兆2,300億円 / 8,200億円 / 12.3億円 / 4,500万円 / 980円."""
    if _bad(v):
        return "—"
    a = abs(float(v))
    if a >= 1e12:
        cho, oku = int(a // 1e12), round((a % 1e12) / 1e8)
        if oku >= 10000:
            cho, oku = cho + 1, 0
        s = f"{cho:,}兆" + (f"{oku:,}億" if oku else "") + "円"
    elif a >= 1e10:
        oku = round(a / 1e8)
        s = "1兆円" if oku >= 10000 else f"{oku:,}億円"
    elif a >= 1e8:
        s = f"{round(a / 1e8, 1):,.1f}億円"
    elif a >= 1e4:
        man = round(a / 1e4)
        s = "1.0億円" if man >= 10000 else f"{man:,}万円"
    else:
        s = f"{round(a):,}円"
    return f"{MINUS}{s}" if float(v) < 0 and s != "0円" else s
