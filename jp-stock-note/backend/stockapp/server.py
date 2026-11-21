"""Entry point: python -m stockapp.server  (always binds 127.0.0.1)."""
from __future__ import annotations

import argparse
import logging

import uvicorn

HOST = "127.0.0.1"  # fixed: requirement 1.2 — never accept non-local connections


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run("stockapp.main:app_factory", factory=True, host=HOST, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
