"""Text normalisation for search (全角半角・大文字小文字の正規化)."""
from __future__ import annotations

import re
import unicodedata

_SPACE_RE = re.compile(r"\s+")


def normalize_text(value: str | None) -> str:
    """NFKC (full-width -> half-width ASCII, half-width kana -> full-width kana),
    casefold, and drop all whitespace (incl. ideographic space)."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    return _SPACE_RE.sub("", text)


def display_code(code: str) -> str:
    """J-Quants uses 5-character codes (4-digit code + trailing check digit,
    usually '0'). Show the familiar 4-character code when the suffix is 0."""
    if len(code) == 5 and code.endswith("0"):
        return code[:4]
    return code
