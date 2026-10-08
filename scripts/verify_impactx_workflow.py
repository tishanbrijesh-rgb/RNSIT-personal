#!/usr/bin/env python3
"""Exercise the authenticated ImpactX judge path without exposing credentials."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env", override=False)

from backend.settings import get_settings


def request_json(
    base_url: str,
    path: str,
    *,
    body: dict | None = None,
    token: str | None = None,
) -> dict | list:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    repository = args.repository.resolve()
    if not repository.is_dir():
        print("FAIL repository does not exist", file=sys.stderr)
        return 2

    settings = get_settings()
    username, account = next(iter(settings.users.items()))
    try:
        session = request_json(
            args.base_url,
            "/api/auth/login",
            body={"username": username, "password": account.password},
        )
        assert isinstance(session, dict)
        token = str(session["access_token"])
        print(f"PASS login role={session['role']}")

        accepted = request_json(
            args.base_url,
            "/api/scan",
            body={"repo_path": str(repository)},
            token=token,
        )
        assert isinstance(accepted, dict)
        scan_id = int(accepted["scan_id"])
        print(f"PASS scan accepted id={scan_id} status={accepted['status']}")

        deadline = time.monotonic() + args.timeout
        terminal = {"completed", "failed", "cancelled", "timed_out"}
        job: dict = {}
        while time.monotonic() < deadline:
            response = request_json(args.base_url, f"/api/scans/{scan_id}", token=token)
            assert isinstance(response, dict)
            job = response
            if job.get("status") in terminal:
                break
            time.sleep(1)
        else:
            print(f"FAIL scan {scan_id} did not reach a terminal state", file=sys.stderr)
            return 1

        print(
            "PASS terminal "
            f"status={job['status']} files={job.get('scanned_files')}/{job.get('in_scope_files')} "
            f"findings={job.get('assets_found')} coverage={job.get('coverage_pct')}"
        )
        if job["status"] != "completed":
            return 1

        history = request_json(args.base_url, "/api/scans?limit=200", token=token)
        assert isinstance(history, list)
        if not any(int(item["id"]) == scan_id for item in history):
            print(f"FAIL scan {scan_id} missing from history", file=sys.stderr)
            return 1
        print("PASS completed scan is inspectable in history")
        return 0
    except (AssertionError, KeyError, urllib.error.URLError, ValueError) as exc:
        print(f"FAIL workflow check: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
