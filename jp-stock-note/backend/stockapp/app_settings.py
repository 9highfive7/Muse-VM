"""Editable app settings (S5). Phase 3: the 日銀短観 assumed USD/JPY rate.

The value is never hard-coded into calculations (要件 3.8): it is read from
``app_settings`` (seeded by migration 0004 with 154.23 /「日銀短観 2026年9月調査
・全産業」) and edited from the status page. Each save records value, survey
label and updated-at (= 入力日, 要件 9.1-8).
"""
from __future__ import annotations

import json
import math
import sqlite3

from .db import utc_now

TANKAN_KEY = "tankan_usdjpy"
TANKAN_DEFAULT = {"value": 154.23, "survey_label": "日銀短観 2026年9月調査・全産業", "origin": "default"}
TANKAN_MAX = 1000.0          # sanity ceiling: rejects typos such as 15423
LABEL_MAX_LEN = 60


class SettingError(ValueError):
    pass


def get_tankan(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value_json, updated_at FROM app_settings WHERE key=?", (TANKAN_KEY,)).fetchone()
    data = dict(TANKAN_DEFAULT) if row is None else json.loads(row["value_json"])
    return {"value": float(data["value"]), "survey_label": data["survey_label"],
            "origin": data.get("origin", "user"), "updated_at": row["updated_at"] if row else None}


def validate_tankan(value, survey_label) -> tuple[float, str]:
    if isinstance(value, bool):
        raise SettingError("想定為替レートは数値で入力してください。")
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise SettingError("想定為替レートは数値で入力してください。") from None
    if math.isnan(v) or math.isinf(v) or v <= 0:
        raise SettingError("想定為替レートは0より大きい数値で入力してください。")
    if v >= TANKAN_MAX:
        raise SettingError(f"想定為替レートが大きすぎます（{TANKAN_MAX:,.0f}円未満で入力してください）。")
    label = (survey_label or "").strip() if isinstance(survey_label, str) else ""
    if not label:
        raise SettingError("調査回（例：日銀短観 2026年9月調査・全産業）を入力してください。")
    if len(label) > LABEL_MAX_LEN:
        raise SettingError(f"調査回は{LABEL_MAX_LEN}文字以内で入力してください。")
    return round(v, 4), label


def set_tankan(conn: sqlite3.Connection, value, survey_label) -> dict:
    v, label = validate_tankan(value, survey_label)
    with conn:
        conn.execute(
            "INSERT INTO app_settings(key, value_json, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (TANKAN_KEY, json.dumps({"value": v, "survey_label": label, "origin": "user"}, ensure_ascii=False),
             utc_now()),
        )
    return get_tankan(conn)


# --------------------------------------------------------------------------- Phase 4: S0 初回説明

FIRST_RUN_KEY = "first_run_ack"


def about() -> dict:
    """S0 text (要件 2.1 S0 / 3.11 拡張版免責), from content/about.json."""
    from .wording import CONTENT_DIR

    data = json.loads((CONTENT_DIR / "about.json").read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def get_first_run(conn: sqlite3.Connection) -> dict:
    version = about()["version"]
    row = conn.execute("SELECT value_json, updated_at FROM app_settings WHERE key=?", (FIRST_RUN_KEY,)).fetchone()
    acked = json.loads(row["value_json"]) if row else None
    required = acked is None or int(acked.get("version", 0)) < version
    return {"required": required, "version": version,
            "acknowledged_at": None if acked is None else acked.get("acknowledged_at"),
            "acknowledged_version": None if acked is None else acked.get("version")}


def ack_first_run(conn: sqlite3.Connection) -> dict:
    version = about()["version"]
    with conn:
        conn.execute(
            "INSERT INTO app_settings(key, value_json, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (FIRST_RUN_KEY, json.dumps({"version": version, "acknowledged_at": utc_now()}), utc_now()),
        )
    return get_first_run(conn)
