#!/usr/bin/env python3
"""
seed_db.py — Run the scanner against ./test-repo and persist results
into Postgres. Run with:  python scripts/seed_db.py
"""

import os
import sys

# Make package imports work
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE)
sys.path.insert(0, os.path.join(_BASE, "scanner"))

from backend.db import Base, engine
from backend.services.scanner_runner import run_scan


def main() -> None:
    # Ensure tables exist (idempotent)
    Base.metadata.create_all(bind=engine)
    print("[seed] DB tables ready")

    test_repo = os.path.join(_BASE, "test-repo")
    if not os.path.isdir(test_repo):
        print(f"[seed] WARN: {test_repo} not found — creating stub")
        os.makedirs(test_repo, exist_ok=True)

    print(f"[seed] Running scanner against {test_repo}")
    result = run_scan(test_repo)
    print(f"[seed] Done: {result}")


if __name__ == "__main__":
    main()
