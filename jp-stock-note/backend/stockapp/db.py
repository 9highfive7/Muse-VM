"""SQLite connection helpers and the migration runner.

Run migrations from the command line with:  python -m stockapp.db
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path | str) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply any migrations/NNNN_*.sql not yet recorded. Returns applied names."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        version = path.stem
        if version in done:
            continue
        # Script and its bookkeeping row commit together (version comes from
        # our own file names, so inlining it is safe).
        conn.executescript(
            "BEGIN;\n"
            + path.read_text(encoding="utf-8")
            + f"\nINSERT INTO schema_migrations(version, applied_at)"
            f" VALUES ('{version}', '{utc_now()}');\nCOMMIT;"
        )
        applied.append(version)
    return applied


def main(argv: list[str]) -> int:
    from .config import load_settings

    settings = load_settings()
    db_path = Path(argv[1]) if len(argv) > 1 else settings.db_path
    conn = connect(db_path)
    applied = migrate(conn)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    print(f"db={db_path} journal_mode={mode} applied={applied or 'none (up to date)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
