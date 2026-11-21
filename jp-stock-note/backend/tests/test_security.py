"""Local-only access and secret hygiene (受け入れ条件①③)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from stockapp import server
from stockapp.config import Settings, load_settings, parse_env_file

APP_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = APP_ROOT / "frontend"
DIST = FRONTEND / "dist"
FORBIDDEN = ["api.jquants.com", "JQUANTS_API_KEY"]


def test_server_binds_loopback_only():
    assert server.HOST == "127.0.0.1"


def test_non_loopback_client_rejected(make_client):
    client = make_client(peer=("192.168.1.10", 50000))
    assert client.get("/api/status").status_code == 403


def test_loopback_client_allowed(make_client):
    assert make_client(peer=("127.0.0.1", 50000)).get("/api/status").status_code == 200
    assert make_client(peer=("::1", 50000)).get("/api/status").status_code == 200


def test_foreign_host_header_rejected(make_client):
    # DNS-rebinding guard: a page on evil.example resolving to 127.0.0.1
    client = make_client(base_url="http://evil.example:8000")
    assert client.get("/api/status").status_code == 400


def test_settings_repr_hides_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text('JQUANTS_API_KEY="super-secret-value"\n', encoding="utf-8")
    old = os.environ.pop("JQUANTS_API_KEY", None)
    try:
        s = load_settings(env)
    finally:
        if old is not None:
            os.environ["JQUANTS_API_KEY"] = old
    assert s.api_key == "super-secret-value"
    assert "super-secret-value" not in repr(s)
    assert parse_env_file(tmp_path / "missing") == {}


def test_spa_fallback_and_api_404(make_settings, make_client, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "assets" / "a.js").write_text("console.log(1)", encoding="utf-8")
    client = make_client(make_settings(frontend_dist=dist))
    assert client.get("/stocks/7203").text == "<html>app</html>"
    assert client.get("/assets/a.js").text == "console.log(1)"
    assert client.get("/api/nope").status_code == 404
    assert client.get("/../../etc/passwd").text == "<html>app</html>"


def _ensure_build() -> None:
    if (DIST / "index.html").is_file():
        return
    npm = shutil.which("npm")
    if npm is None:
        pytest.fail("frontend/dist is missing and npm is not available to build it")
    if not (FRONTEND / "node_modules").is_dir():
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=FRONTEND, check=True)
    subprocess.run([npm, "run", "build"], cwd=FRONTEND, check=True)


def test_build_output_has_no_jquants_host_or_key_name():
    _ensure_build()
    files = [p for p in DIST.rglob("*") if p.is_file()]
    assert files, "build output is empty"
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in FORBIDDEN:
            assert needle not in text, f"{needle!r} found in {path.relative_to(APP_ROOT)}"
        assert not re.search(r"x-api-key", text, re.IGNORECASE), f"x-api-key found in {path}"


def test_frontend_source_only_calls_same_origin_api():
    src = "\n".join(p.read_text(encoding="utf-8") for p in (FRONTEND / "src").rglob("*.ts*"))
    assert "jquants" not in src.lower()
    assert not re.search(r"fetch\(\s*['\"`]https?://", src)


def test_real_env_file_is_not_shipped():
    assert (APP_ROOT / ".env.example").is_file()
    example = parse_env_file(APP_ROOT / ".env.example")
    assert example.get("JQUANTS_API_KEY", "") == ""
    gitignore = (APP_ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in (".env", "data/", "node_modules", ".venv"):
        assert entry in gitignore


def test_default_settings_are_local_only():
    s = Settings()
    assert s.allowed_clients == frozenset({"127.0.0.1", "::1"})
    assert "0.0.0.0" not in s.allowed_hosts
