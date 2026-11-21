"""Phase 2 ingestion: financial summaries (/v2/fins/summary) by code and by
date, with correction detection (要件 4.1・4.2・4.6).

* Every response row is stored verbatim in ``raw_json`` plus the mapped
  columns (docs/api-field-mapping.md). Empty strings become NULL, never 0.
* The primary key is the disclosure number. When a row with an already stored
  disclosure number arrives with a different content hash (SHA-256 of the
  canonical JSON), the stored row is overwritten with the new values and the
  old values are appended to ``fins_revision_log`` (field, old, new).
* On-demand (P1): ``ensure_fins`` fetches a code's summaries when none are
  stored, or when the last by-code fetch is older than 7 days (要件 4.2).

All HTTP goes through ``JQuantsClient`` (shared queue + token bucket).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import utc_now
from .ingest import _run
from .jquants.client import JQuantsClient
from .jquants.ratelimit import Priority
from .queries import resolve_code

REFETCH_AFTER_DAYS = 7  # 要件 4.2: re-fetch by code only when the last fetch is older than this

# logical column -> candidate API fields (first non-empty wins). 🟡 = name not
# yet confirmed against a live response (see docs/api-field-mapping.md).
TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "disclosure_no": ("DiscNo",),
    "code": ("Code",),
    "disclosed_date": ("DiscDate",),
    "disclosed_time": ("DiscTime",),
    "doc_type": ("DocType",),
    "period_type": ("CurPerType",),
    "period_start": ("CurPerSt",),
    "period_end": ("CurPerEn",),
    "fy_start": ("CurFYSt",),
    "fy_end": ("CurFYEn",),
    "next_fy_start": ("NxtFYSt",),
    "next_fy_end": ("NxtFYEn",),
}
NUM_FIELDS: dict[str, tuple[str, ...]] = {
    "net_sales": ("Sales",),
    "operating_profit": ("OP",),
    "ordinary_profit": ("OdP",),
    "profit": ("NP",),
    "eps": ("EPS",),
    "total_assets": ("TA",),
    "equity": ("Eq",),
    "equity_ratio": ("EqAR",),
    "bps": ("BPS",),
    "cf_operating": ("CFO",),
    "cf_investing": ("CFI",),
    "cf_financing": ("CFF",),
    "cash_equivalents": ("CashEq",),
    "dividend_annual": ("DivAnn",),
    "forecast_net_sales": ("FSales",),
    "forecast_operating_profit": ("FOP",),
    "forecast_ordinary_profit": ("FOdP",),          # 🟡
    "forecast_profit": ("FNP",),                    # 🟡
    "forecast_eps": ("FEPS",),                      # 🟡
    "forecast_dividend_annual": ("FDivAnn",),       # 🟡
    "next_forecast_net_sales": ("NxFSales",),       # 🟡
    "next_forecast_operating_profit": ("NxFOP",),   # 🟡
    "next_forecast_ordinary_profit": ("NxFOdP",),   # 🟡
    "next_forecast_profit": ("NxFNp", "NxFNP"),     # 🟡
    "next_forecast_eps": ("NxFEPS",),               # 🟡
    "next_forecast_dividend_annual": ("NxFDivAnn",),  # 🟡
}
FIELD_TO_COLUMN = {f: col for col, fields in {**TEXT_FIELDS, **NUM_FIELDS}.items() for f in fields}

STANDARDS = ("JP", "IFRS", "US", "JMIS")

_COLUMNS = (
    list(TEXT_FIELDS) + list(NUM_FIELDS)
    + ["doc_kind", "consolidation", "accounting_standard", "raw_json", "content_sha256", "fetched_at",
       "first_fetched_at", "updated_at"]
)

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first(row: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return None


def parse_doc_type(doc_type: str | None) -> dict[str, str | None]:
    """'3QFinancialStatements_Consolidated_IFRS' -> statement / consolidated / IFRS."""
    if not doc_type:
        return {"doc_kind": "other", "consolidation": None, "accounting_standard": None}
    parts = doc_type.split("_")
    if "FinancialStatements" in parts[0]:
        consolidation = ("non_consolidated" if "NonConsolidated" in parts
                         else "consolidated" if "Consolidated" in parts else None)
        standard = next((p for p in parts[1:] if p in STANDARDS), None)
        if standard is None and len(parts) > 2:
            standard = parts[-1]   # e.g. REIT: kept verbatim, compared as-is
        return {"doc_kind": "statement", "consolidation": consolidation, "accounting_standard": standard}
    if "DividendForecastRevision" in doc_type:
        return {"doc_kind": "dividend_revision", "consolidation": None, "accounting_standard": None}
    if "ForecastRevision" in doc_type:
        return {"doc_kind": "forecast_revision", "consolidation": None, "accounting_standard": None}
    return {"doc_kind": "other", "consolidation": None, "accounting_standard": None}


def canonical_json(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(row: dict) -> str:
    return hashlib.sha256(canonical_json(row).encode("utf-8")).hexdigest()


def map_row(row: dict) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    for col, keys in TEXT_FIELDS.items():
        v = _first(row, keys)
        out[col] = None if v is None else str(v)
    if not (out["disclosure_no"] and out["code"]):
        return None
    for col, keys in NUM_FIELDS.items():
        out[col] = _num(_first(row, keys))
    out.update(parse_doc_type(out["doc_type"]))
    return out


def diff_fields(old: dict, new: dict) -> list[dict]:
    changes = []
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            changes.append({"field": key, "column": FIELD_TO_COLUMN.get(key),
                            "old": old.get(key), "new": new.get(key)})
    return changes


def store_fins_summary(conn: sqlite3.Connection, rows: list[dict[str, Any]], fetched_at: str,
                       only_code: str | None = None) -> dict:
    """Upsert by disclosure number. Same number + different content hash ->
    overwrite and append the old values to fins_revision_log."""
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "revisions": [], "last_date": None}
    for raw in rows:
        mapped = map_row(raw)
        if mapped is None or (only_code and mapped["code"] != only_code):
            continue
        sha = content_hash(raw)
        raw_json = canonical_json(raw)
        old = conn.execute(
            "SELECT raw_json, content_sha256, fetched_at, first_fetched_at FROM fins_summary WHERE disclosure_no = ?",
            (mapped["disclosure_no"],),
        ).fetchone()
        first_fetched = fetched_at
        if old is not None:
            first_fetched = old["first_fetched_at"] or old["fetched_at"]
            if old["content_sha256"] == sha:
                conn.execute("UPDATE fins_summary SET fetched_at = ? WHERE disclosure_no = ?",
                             (fetched_at, mapped["disclosure_no"]))
                stats["unchanged"] += 1
                continue
            try:
                old_raw = json.loads(old["raw_json"] or "{}")
            except json.JSONDecodeError:
                old_raw = {}
            changes = diff_fields(old_raw if isinstance(old_raw, dict) else {}, raw)
            conn.execute(
                "INSERT INTO fins_revision_log(disclosure_no, code, detected_at, changes_json, old_sha256,"
                " new_sha256, old_raw_json, old_fetched_at) VALUES (?,?,?,?,?,?,?,?)",
                (mapped["disclosure_no"], mapped["code"], fetched_at, json.dumps(changes, ensure_ascii=False),
                 old["content_sha256"], sha, old["raw_json"], old["fetched_at"]),
            )
            stats["updated"] += 1
            stats["revisions"].append({"disclosure_no": mapped["disclosure_no"], "fields": [c["field"] for c in changes]})
        else:
            stats["inserted"] += 1
        values = {**mapped, "raw_json": raw_json, "content_sha256": sha, "fetched_at": fetched_at,
                  "first_fetched_at": first_fetched, "updated_at": fetched_at}
        conn.execute(
            f"INSERT OR REPLACE INTO fins_summary({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
            [values[c] for c in _COLUMNS],
        )
        d = mapped["disclosed_date"]
        if d and (stats["last_date"] is None or d > stats["last_date"]):
            stats["last_date"] = d
    return stats


def _update_state(conn: sqlite3.Connection, code: str, reason: str) -> None:
    first, last, count = conn.execute(
        "SELECT MIN(disclosed_date), MAX(disclosed_date), COUNT(*) FROM fins_summary WHERE code = ?", (code,)
    ).fetchone()
    conn.execute(
        "INSERT INTO history_state(series, full_fetched_at, full_reason, first_date, last_date, row_count)"
        " VALUES (?,?,?,?,?,?) ON CONFLICT(series) DO UPDATE SET full_fetched_at=excluded.full_fetched_at,"
        " full_reason=excluded.full_reason, first_date=excluded.first_date, last_date=excluded.last_date,"
        " row_count=excluded.row_count",
        (f"fins:{code}", utc_now(), reason, first, last, count),
    )


def fetch_fins_by_code(conn: sqlite3.Connection, client: JQuantsClient, code: str,
                       priority: int = Priority.P1_ON_DEMAND, reason: str = "on_demand") -> dict:
    code = resolve_code(code)
    info: dict[str, Any] = {}

    def store(rows, fetched_at):
        stats = store_fins_summary(conn, rows, fetched_at, only_code=code)
        _update_state(conn, code, reason)
        info.update(stats)
        return stats["last_date"]

    out = _run(conn, "fins_summary_by_code", {"code": code, "reason": reason}, priority,
               lambda: client.fins_summary(code=code, priority=priority), store)
    out.update(code=code, inserted=info.get("inserted", 0), updated=info.get("updated", 0),
               unchanged=info.get("unchanged", 0), revisions=info.get("revisions", []))
    return out


def fetch_fins_by_date(conn: sqlite3.Connection, client: JQuantsClient, day: str,
                       priority: int = Priority.P2_NIGHTLY) -> dict:
    info: dict[str, Any] = {}

    def store(rows, fetched_at):
        stats = store_fins_summary(conn, rows, fetched_at)
        info.update(stats)
        return stats["last_date"]

    out = _run(conn, "fins_summary_by_date", {"date": day}, priority,
               lambda: client.fins_summary(date=day, priority=priority), store)
    out.update(date=day, inserted=info.get("inserted", 0), updated=info.get("updated", 0),
               unchanged=info.get("unchanged", 0), revisions=info.get("revisions", []))
    return out


def last_fetch(conn: sqlite3.Connection, code: str) -> str | None:
    row = conn.execute("SELECT full_fetched_at FROM history_state WHERE series = ?", (f"fins:{code}",)).fetchone()
    return row[0] if row and row[0] else None


def ensure_fins(conn: sqlite3.Connection, client: JQuantsClient, code: str, *, force: bool = False,
                now: datetime | None = None) -> dict:
    """On-demand (P1) when a stock page opens: fetch by code if nothing is
    stored, or the last by-code fetch is older than 7 days, or ``force``
    (manual re-fetch). Otherwise no API call."""
    code = resolve_code(code)
    now = now or datetime.now(timezone.utc)
    with _lock_for(code):
        has = conn.execute("SELECT 1 FROM fins_summary WHERE code = ? LIMIT 1", (code,)).fetchone() is not None
        last = last_fetch(conn, code)
        stale = last is None or datetime.fromisoformat(last) < now - timedelta(days=REFETCH_AFTER_DAYS)
        step = None
        if force or not has or stale:
            step = fetch_fins_by_code(conn, client, code, Priority.P1_ON_DEMAND,
                                      reason="manual" if force else ("on_demand" if not has else "refresh_7d"))
        has_after = conn.execute("SELECT 1 FROM fins_summary WHERE code = ? LIMIT 1", (code,)).fetchone() is not None
    failed = step is not None and step["result"] != "ok"
    return {
        "code": code,
        "state": "failed" if failed else ("fetched" if step else "stored"),
        "has_fins": has_after,
        "last_fetched_at": last_fetch(conn, code),
        "revisions_detected": len(step.get("revisions", [])) if step and not failed else 0,
        "error_reason": step.get("error_reason") if failed else None,
    }


def revision_log(conn: sqlite3.Connection, code: str) -> list[dict]:
    out = []
    for r in conn.execute(
        "SELECT l.*, f.disclosed_date, f.period_type, f.doc_type FROM fins_revision_log l"
        " LEFT JOIN fins_summary f ON f.disclosure_no = l.disclosure_no"
        " WHERE l.code = ? ORDER BY l.detected_at DESC, l.id DESC", (resolve_code(code),)
    ):
        out.append({
            "disclosure_no": r["disclosure_no"], "detected_at": r["detected_at"],
            "disclosed_date": r["disclosed_date"], "period_type": r["period_type"], "doc_type": r["doc_type"],
            "changes": json.loads(r["changes_json"]),
        })
    return out
