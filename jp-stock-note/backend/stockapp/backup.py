"""DB generation backup (要件 7.3 / 9.2): a consistent copy of data/app.db into
data/backup/ via SQLite's online backup API, keeping the newest 7 copies.
Run at the end of the nightly batch; also ``python -m stockapp.backup``."""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

KEEP = 7
PREFIX = "app-"


def backup_db(db_path: Path | str, backup_dir: Path | str | None = None, keep: int = KEEP,
              now: datetime | None = None) -> dict:
    db_path = Path(db_path)
    backup_dir = Path(backup_dir) if backup_dir else db_path.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"{PREFIX}{stamp}.db"
    n = 1
    while dest.exists():                      # two runs within the same second
        dest = backup_dir / f"{PREFIX}{stamp}-{n}.db"
        n += 1
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    files = sorted(backup_dir.glob(f"{PREFIX}*.db"))
    removed = []
    for old in files[:-keep] if keep > 0 else []:
        old.unlink()
        removed.append(old.name)
    return {"path": str(dest), "kept": [p.name for p in sorted(backup_dir.glob(f"{PREFIX}*.db"))], "removed": removed}


def main(argv: list[str]) -> int:
    from .config import load_settings

    out = backup_db(load_settings().db_path)
    print(f"backup={out['path']} kept={len(out['kept'])} removed={out['removed'] or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
