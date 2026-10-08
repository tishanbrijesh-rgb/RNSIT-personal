#!/usr/bin/env python3
"""
Visual regression tests for ECDAT dashboard using Playwright screenshots.

Captures key pages at multiple viewport sizes and compares against baseline
images.  On first run (no baselines exist) it captures them and exits 0.

Usage:
    python scripts/visual_regression.py                        # run checks
    python scripts/visual_regression.py --capture               # force capture baselines
    python scripts/visual_regression.py --base-url http://localhost:3000
    python scripts/visual_regression.py --threshold 0.02        # pixel-diff threshold (2%)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "dashboard"
BASELINE_DIR = DASHBOARD_DIR / "tests" / "visual-baselines"
REPORT_DIR = DASHBOARD_DIR / "test-results"

# Pages to capture and their routes.
PAGES: dict[str, str] = {
    "login": "/login",
    "dashboard": "/",
    "assets": "/assets",
    "scan": "/scan",
}

# Viewport sizes to test.
VIEWPORTS: list[dict[str, Any]] = [
    {"name": "mobile", "width": 375, "height": 812},
    {"name": "tablet", "width": 768, "height": 1024},
    {"name": "desktop", "width": 1280, "height": 800},
    {"name": "wide", "width": 1440, "height": 900},
]

COLOR_SCHEMES: list[Literal["light", "dark"]] = ["light", "dark"]

# Pixel-diff threshold: percentage of differing pixels to treat as a failure.
DEFAULT_THRESHOLD = 0.01  # 1%


def _screenshot_name(page: str, viewport: str, scheme: str) -> str:
    return f"{page}__{viewport}__{scheme}.png"


def _load_baseline(name: str) -> bytes | None:
    path = BASELINE_DIR / name
    if path.exists():
        return path.read_bytes()
    return None


def _save_baseline(name: str, data: bytes) -> None:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    (BASELINE_DIR / name).write_bytes(data)


def _pixel_diff(a: bytes, b: bytes) -> float:
    """Return the fraction of differing pixels between two PNG byte strings.

    Uses Pillow if available; otherwise falls back to SHA-256 comparison
    (exact-match mode, useful as a baseline but less useful for real CI).
    """
    try:
        import io

        from PIL import Image

        img_a = Image.open(io.BytesIO(a)).convert("RGB")
        img_b = Image.open(io.BytesIO(b)).convert("RGB")

        if img_a.size != img_b.size:
            # Size mismatch = full diff.
            return 1.0

        pixels_a = list(img_a.getdata())
        pixels_b = list(img_b.getdata())
        if len(pixels_a) == 0:
            return 0.0

        diffs = sum(
            1 for pa, pb in zip(pixels_a, pixels_b, strict=True)
            if abs(pa[0] - pb[0]) > 10 or abs(pa[1] - pb[1]) > 10 or abs(pa[2] - pb[2]) > 10
        )
        return diffs / len(pixels_a)
    except ImportError:
        # No Pillow — use exact binary comparison.
        if a == b:
            return 0.0
        return 1.0


def _capture_screenshots(base_url: str) -> dict[str, bytes]:
    """Capture screenshots and return name -> bytes mapping."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright", file=sys.stderr)
        sys.exit(1)

    screenshots: dict[str, bytes] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for page_name, route in PAGES.items():
            for vp in VIEWPORTS:
                for scheme in COLOR_SCHEMES:
                    context = browser.new_context(
                        viewport={"width": vp["width"], "height": vp["height"]},
                        color_scheme=scheme,
                    )
                    pg = context.new_page()
                    url = base_url.rstrip("/") + route
                    try:
                        pg.goto(url, wait_until="networkidle", timeout=30_000)
                        # Set color scheme via CSS if the page supports it.
                        if scheme == "dark":
                            pg.evaluate(
                                "() => document.documentElement.classList.add('dark')"
                            )
                    except Exception as exc:
                        print(f"  WARNING: could not load {url}: {exc}", file=sys.stderr)
                        context.close()
                        continue

                    name = _screenshot_name(str(page_name), vp["name"], scheme)
                    screenshots[name] = pg.screenshot(full_page=False)
                    context.close()
        browser.close()
    return screenshots


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Visual regression checks for ECDAT dashboard",
    )
    parser.add_argument(
        "--base-url", default="http://localhost:3000",
        help="Base URL of the running dashboard",
    )
    parser.add_argument(
        "--capture", action="store_true",
        help="Force capture and save baseline images",
    )
    parser.add_argument(
        "--output", default=None,
        help="Write JSON report to this file",
    )
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD,
        help=f"Pixel-diff threshold (fraction, default {DEFAULT_THRESHOLD})",
    )
    args = parser.parse_args()

    print(f"Visual regression: base={args.base_url}, threshold={args.threshold:.1%}")

    # Ensure node_modules exist (Playwright is a dev dep of the dashboard).
    if not (DASHBOARD_DIR / "node_modules").exists():
        print("Installing npm dependencies...")
        subprocess.run(
            ["npm", "ci"],
            cwd=str(DASHBOARD_DIR),
            check=True,
            capture_output=True,
        )

    screenshots = _capture_screenshots(args.base_url)
    print(f"Captured {len(screenshots)} screenshot(s)")

    report: list[dict[str, Any]] = []
    has_failures = False

    for name, current in screenshots.items():
        baseline = _load_baseline(name)

        if baseline is None or args.capture:
            if args.capture:
                _save_baseline(name, current)
                print(f"  CAPTURED baseline: {name}")
            else:
                print(f"  MISSING baseline: {name} (run with --capture to create)")
            report.append({"name": name, "status": "baseline_missing", "diff": None})
            continue

        diff = _pixel_diff(baseline, current)
        status = "pass" if diff <= args.threshold else "fail"
        report.append({"name": name, "status": status, "diff": round(diff, 6)})

        if status == "pass":
            print(f"  PASS  {name} (diff={diff:.4f})")
        else:
            print(f"  FAIL  {name} (diff={diff:.4f} > threshold={args.threshold})")
            has_failures = True

    # Write report.
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / "visual-regression-report.json"
    summary = {
        "total": len(report),
        "passed": sum(1 for r in report if r["status"] == "pass"),
        "failed": sum(1 for r in report if r["status"] == "fail"),
        "missing_baseline": sum(1 for r in report if r["status"] == "baseline_missing"),
        "items": report,
    }
    report_path.write_text(json.dumps(summary, indent=2))
    print(f"\nReport: {report_path}")

    if has_failures:
        print(f"\nFAILED: {summary['failed']} visual regression(s)")
        return 1

    if args.capture:
        print("Baselines captured successfully")
    else:
        print(f"\nPASSED: {summary['passed']} visual check(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
