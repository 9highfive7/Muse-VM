"""Settings loaded from the environment and the project-root .env file.

The J-Quants API key is read here and only here. It is held in a private
attribute, excluded from repr(), and never written to logs or the DB.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "app.db"
DEFAULT_FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

API_KEY_ENV = "JQUANTS_API_KEY"


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser (no python-dotenv dependency)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


@dataclass
class Settings:
    db_path: Path = DEFAULT_DB_PATH
    frontend_dist: Path = DEFAULT_FRONTEND_DIST
    jquants_base_url: str = "https://api.jquants.com"
    # FRED (keyless). Only the server's fetch layer talks to it (要件 7.1).
    fred_base_url: str = "https://fred.stlouisfed.org"
    # Client hosts allowed to talk to the server (defence in depth on top of
    # binding to 127.0.0.1).
    allowed_clients: frozenset[str] = frozenset({"127.0.0.1", "::1"})
    # Host header allow-list (guards against DNS rebinding).
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    # Start the background sync thread (startup catch-up + 20:00 JST nightly).
    # Only the production app factory turns this on; tests never do.
    background_jobs: bool = False
    _api_key: str | None = field(default=None, repr=False)

    @property
    def api_key(self) -> str | None:
        return self._api_key or None

    @property
    def api_key_configured(self) -> bool:
        return bool(self._api_key)


def load_settings(env_file: Path | None = None, **overrides) -> Settings:
    env_values = parse_env_file(env_file or DEFAULT_ENV_FILE)
    api_key = os.environ.get(API_KEY_ENV) or env_values.get(API_KEY_ENV) or None
    db_path = os.environ.get("STOCKAPP_DB_PATH") or env_values.get("STOCKAPP_DB_PATH")
    settings = Settings(_api_key=api_key)
    if db_path:
        settings.db_path = Path(db_path)
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings
