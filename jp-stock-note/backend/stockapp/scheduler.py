"""Background sync thread: startup catch-up, then the nightly batch at
20:00 JST (要件 4.2), draining the persistent fetch queue in between.

A small in-process thread replaces APScheduler (one job, no extra dependency).
It is only started by the production app factory, never by tests.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from . import bars
from .config import Settings
from .db import connect

log = logging.getLogger(__name__)


def seconds_until_next_run(now: datetime, hour: int = bars.PUBLISH_CUTOFF_HOUR_JST) -> float:
    now = now.astimezone(bars.JST)
    target = now.replace(hour=hour, minute=5, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


class BackgroundSync:
    def __init__(self, settings: Settings, get_client, get_fred_client=None):
        self.settings = settings
        self.get_client = get_client
        self.get_fred_client = get_fred_client
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_startup: dict | None = None

    def start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, name="stockapp-sync", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()

    def wake(self) -> None:
        """Process newly queued jobs (e.g. a split re-fetch) without waiting."""
        self.wake_event.set()

    def _loop(self) -> None:
        conn = connect(self.settings.db_path)
        try:
            client = self.get_client()
            try:
                self.last_startup = bars.startup_sync(conn, client)
                log.info("startup catch-up: queued=%s topix=%s", self.last_startup.get("queued"),
                         self.last_startup.get("topix_job"))
            except Exception:
                log.exception("startup catch-up failed")
            self._fred("startup")
            while not self.stop_event.is_set():
                try:
                    bars.run_queue(conn, client, stop=self.stop_event)
                except Exception:
                    log.exception("queue run failed")
                wait = seconds_until_next_run(bars.now_jst())
                woke = self.wake_event.wait(wait)
                self.wake_event.clear()
                if self.stop_event.is_set():
                    break
                if not woke:  # timer fired: nightly batch
                    try:
                        bars.nightly(conn, client)
                    except Exception:
                        log.exception("nightly queueing failed")
                    self._fred("nightly")
                    try:
                        bars.run_queue(conn, client, stop=self.stop_event)
                    except Exception:
                        log.exception("queue run failed")
                    self._backup()
        finally:
            conn.close()

    def _backup(self) -> None:
        """要件 7.3: end of the nightly batch -> data/backup/ (7 generations)."""
        from .backup import backup_db

        try:
            out = backup_db(self.settings.db_path)
            log.info("db backup: %s (removed %s)", out["path"], out["removed"])
        except Exception:
            log.exception("db backup failed")

    def _fred(self, mode: str) -> None:
        """FRED (要件 4.2 ⑦ / 4.5): its own queue and connection; a failure is
        logged per series and never stops the J-Quants work."""
        if self.get_fred_client is None:
            return
        from . import fred

        conn = connect(self.settings.db_path)
        try:
            client = self.get_fred_client()
            out = fred.startup(conn, client) if mode == "startup" else fred.nightly(conn, client)
            if out:
                log.info("fred %s: %s", mode, [(r["series_id"], r["result"]) for r in out["series"]])
        except Exception:
            log.exception("fred %s failed", mode)
        finally:
            conn.close()
