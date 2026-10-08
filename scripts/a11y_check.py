#!/usr/bin/env python3
"""
Authenticated accessibility checks for ECDAT using axe-core via Playwright.

Logs in via the API with credentials from environment variables, then runs
axe-core against every authenticated route.  Tests desktop and mobile
viewports, light and dark colour schemes, keyboard navigation, and
reduced-motion preferences.

Environment variables
---------------------
ECDAT_A11Y_USERNAME   Dashboard username (required for auth tests).
ECDAT_A11Y_PASSWORD   Dashboard password (required for auth tests).
ECDAT_A11Y_BASE_URL   Base URL of the running dashboard (default http://localhost:3000).
ECDAT_A11Y_OUTPUT     Directory for axe JSON reports (default .a11y-reports).

Usage
-----
    python scripts/a11y_check.py                              # all routes, both viewports, both themes
    python scripts/a11y_check.py --pages dashboard login       # specific pages only
    python scripts/a11y_check.py --skip-auth                   # unauthenticated pages only
    python scripts/a11y_check.py --viewport mobile             # mobile only
    python scripts/a11y_check.py --theme dark                  # dark mode only
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "dashboard"

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

# Pages that do not require authentication.
PUBLIC_PAGES: dict[str, str] = {
    "login": "/login",
}

# Pages that require an authenticated session.
PROTECTED_PAGES: dict[str, str] = {
    "dashboard": "/",
    "assets": "/assets",
    "scan": "/scan",
    "reports": "/reports",
    "cbom": "/cbom",
}

# Protected pages that need a numeric ID from the API.  The ID is resolved
# at runtime; if the API returns no records the route is skipped.
ID_PAGES: dict[str, str] = {
    "asset-detail": "/assets/{id}",
    "scan-detail": "/scans/{id}",
}

# Full route set (merged for convenience).
ALL_PAGES = {**PUBLIC_PAGES, **PROTECTED_PAGES, **ID_PAGES}

# ---------------------------------------------------------------------------
# Viewport / theme matrix
# ---------------------------------------------------------------------------

VIEWPORTS: dict[str, dict[str, int]] = {
    "desktop": {"width": 1280, "height": 800},
    "mobile": {"width": 375, "height": 812},
}

THEMES: tuple[str, ...] = ("light", "dark")

# Axe severity levels that cause failure.
_FAIL_SEVERITIES = {"serious", "critical"}

# Axe rules we intentionally disable (cosmetic / noisy on SPAs).
_AXE_DISABLED_RULES: dict[str, dict[str, Any]] = {
    "region": {"enabled": False},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env(name: str, default: str = "") -> str:
    """Read an environment variable, falling back to *default*."""
    return os.environ.get(name) or default


def _require_creds() -> tuple[str, str] | None:
    """Return (username, password) if both env vars are set, else None."""
    username = _env("ECDAT_A11Y_USERNAME")
    password = _env("ECDAT_A11Y_PASSWORD")
    if username and password:
        return username, password
    return None


def _output_dir() -> Path:
    raw = _env("ECDAT_A11Y_OUTPUT", ".a11y-reports")
    path = REPO_ROOT / raw
    path.mkdir(parents=True, exist_ok=True)
    return path


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _axe_script() -> str:
    """Return the axe-core injection script as a string."""
    axe_pkg = DASHBOARD_DIR / "node_modules" / "axe-core"
    if not axe_pkg.exists():
        _install_axe()
    axe_file = axe_pkg / "axe.min.js"
    if not axe_file.exists():
        print("ERROR: axe-core not found after install", file=sys.stderr)
        sys.exit(1)
    return axe_file.read_text(encoding="utf-8")


def _install_axe() -> None:
    """Install axe-core if not already present."""
    node_modules = DASHBOARD_DIR / "node_modules"
    if not node_modules.exists():
        print("Installing npm dependencies...")
        _run_npm(["ci"])
    print("Installing axe-core...")
    _run_npm(["install", "--save-dev", "axe-core@^4.10"])


def _run_npm(args: list[str]) -> None:
    import subprocess

    executable = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if executable is None:
        raise RuntimeError("npm executable not found on PATH")
    subprocess.run(
        [executable, *args],
        cwd=str(DASHBOARD_DIR),
        check=True,
        capture_output=True,
    )


def _ensure_playwright_browsers() -> None:
    """Install Chromium if not already cached."""
    import subprocess

    try:
        import playwright  # noqa: F401
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"WARNING: playwright install chromium failed: {result.stderr}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _api_login(page: Any, base_url: str, username: str, password: str) -> str:
    """Authenticate via the login API and return the bearer token."""
    api_base = base_url.rstrip("/")
    response = page.request.post(
        f"{api_base}/api/auth/login",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"username": username, "password": password}),
    )
    if not response.ok:
        print(
            f"ERROR: login API returned {response.status}: {response.text()}",
            file=sys.stderr,
        )
        sys.exit(1)
    body = response.json()
    token = body.get("access_token", "")
    if not token:
        print("ERROR: login response missing access_token", file=sys.stderr)
        sys.exit(1)
    return token


# ---------------------------------------------------------------------------
# ID resolution
# ---------------------------------------------------------------------------


def _resolve_ids(page: Any, base_url: str) -> dict[str, int]:
    """Fetch IDs from the API for routes that need them.

    Returns a mapping of route-key → id (e.g. ``{"asset-detail": 1}``).
    Routes whose API returns no records are omitted.
    """
    ids: dict[str, int] = {}
    api_base = base_url.rstrip("/")

    # Assets.
    try:
        resp = page.request.get(f"{api_base}/api/assets?limit=1")
        if resp.ok:
            data = resp.json()
            items = data.get("items", [])
            if items:
                ids["asset-detail"] = items[0]["id"]
    except Exception:
        pass

    # Scans.
    try:
        resp = page.request.get(f"{api_base}/api/scans")
        if resp.ok:
            data = resp.json()
            items = data if isinstance(data, list) else data.get("items", [])
            if items:
                ids["scan-detail"] = items[0]["id"]
    except Exception:
        pass

    return ids


# ---------------------------------------------------------------------------
# Axe runner
# ---------------------------------------------------------------------------


def _run_axe(
    page: Any,
    route_name: str,
    route_path: str,
    base_url: str,
) -> dict[str, Any]:
    """Navigate to *route_path* and return the axe result dict."""
    url = base_url.rstrip("/") + route_path
    print(f"    Checking {route_name}: {url}")

    try:
        page.goto(url, wait_until="networkidle", timeout=30_000)
    except Exception as exc:
        print(f"    WARNING: could not load {url}: {exc}", file=sys.stderr)
        return {"error": str(exc), "violations": [], "passes": 0, "inapplicable": 0}

    page.evaluate(_axe_script())
    axe_result = page.evaluate("""
        () => {
            return axe.run(document, {
                runOnly: {
                    type: 'tag',
                    values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']
                },
                    rules: __DISABLED_RULES__
            }).then(result => {
                return {
                    violations: result.violations.map(v => ({
                        id: v.id,
                        impact: v.impact,
                        description: v.description,
                        nodes: v.nodes.length,
                        html: v.nodes.slice(0, 3).map(n => n.html.slice(0, 200))
                    })),
                    passes: result.passes.length,
                    inapplicable: result.inapplicable.length,
                };
            }).catch(err => ({ error: err.message, violations: [], passes: 0, inapplicable: 0 }));
        }
    """.replace("__DISABLED_RULES__", json.dumps(_AXE_DISABLED_RULES)))

    violations = axe_result.get("violations", [])
    if violations:
        print(f"    {len(violations)} violation(s) found")
        for v in violations[:5]:
            print(f"      - [{v.get('impact', '?')}] {v['id']}: {v['description'][:80]}")
    else:
        print(f"    PASS ({axe_result.get('passes', 0)} checks)")

    return axe_result


# ---------------------------------------------------------------------------
# Keyboard navigation check
# ---------------------------------------------------------------------------


def _check_keyboard(page: Any) -> dict[str, Any]:
    """Verify keyboard navigation basics: skip-link, tabbable elements, focus order."""
    result: dict[str, Any] = {
        "skip_link_present": False,
        "tabbable_count": 0,
        "focus_visible_supported": False,
        "issues": [],
    }

    # Check skip link.
    try:
        skip = page.locator('a[href="#main-content"], a.skip-link').count()
        result["skip_link_present"] = skip > 0
        if skip == 0:
            result["issues"].append("No skip-to-content link found")
    except Exception:
        pass

    # Count tabbable elements.
    try:
        tabbable = page.evaluate("""
            () => {
                const els = document.querySelectorAll(
                    'a[href], button:not([disabled]), input:not([disabled]), ' +
                    'select:not([disabled]), textarea:not([disabled]), ' +
                    '[tabindex]:not([tabindex="-1"])'
                );
                return els.length;
            }
        """)
        result["tabbable_count"] = tabbable
        if tabbable == 0:
            result["issues"].append("No tabbable interactive elements found")
    except Exception:
        pass

    # Check for :focus-visible support (CSS).
    try:
        has_focus_visible = page.evaluate("""
            () => {
                for (const sheet of document.styleSheets) {
                    try {
                        for (const rule of sheet.cssRules) {
                            if (rule.selectorText && rule.selectorText.includes(':focus-visible')) {
                                return true;
                            }
                        }
                    } catch { /* cross-origin */ }
                }
                return false;
            }
        """)
        result["focus_visible_supported"] = has_focus_visible
        if not has_focus_visible:
            result["issues"].append("No :focus-visible styles detected")
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Reduced-motion check
# ---------------------------------------------------------------------------


def _check_reduced_motion(page: Any) -> dict[str, Any]:
    """Verify that prefers-reduced-motion is respected."""
    return page.evaluate("""
        () => {
            const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
            let found = false;
            for (const sheet of document.styleSheets) {
                try {
                    for (const rule of sheet.cssRules) {
                        if (rule.media && rule.media.mediaText.includes('prefers-reduced-motion')) {
                            found = true;
                            break;
                        }
                    }
                } catch { /* cross-origin */ }
                if (found) break;
            }
            return {
                media_query_supported: motionQuery.media === 'prefers-reduced-motion',
                stylesheet_respects_reduced_motion: found,
            };
        }
    """)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_checks(
    base_url: str,
    pages: dict[str, str],
    viewports: dict[str, dict[str, int]],
    themes: tuple[str, ...],
    skip_auth: bool = False,
    creds: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Run the full a11y suite and return the results dict."""
    from playwright.sync_api import sync_playwright

    results: dict[str, Any] = {
        "timestamp": _timestamp(),
        "base_url": base_url,
        "skipped_auth": skip_auth,
        "viewport_matrix": list(viewports.keys()),
        "theme_matrix": list(themes),
        "pages": {},
        "summary": {
            "total_checks": 0,
            "passed": 0,
            "failed": 0,
            "errors": 0,
        },
    }

    token: str | None = None
    pw_instance = sync_playwright().start()
    browser = pw_instance.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )

    try:
        if creds and not skip_auth:
            print("\nAuthenticating...")
            auth_ctx = browser.new_context(viewport=viewports["desktop"])
            auth_page = auth_ctx.new_page()
            token = _api_login(auth_page, base_url, creds[0], creds[1])
            print("  Authenticated (API login OK).")
            auth_page.close()
            auth_ctx.close()

        # Resolve IDs for routes that need them.
        id_map: dict[str, int] = {}
        if token:
            resolve_ctx = browser.new_context(viewport=viewports["desktop"])
            resolve_page = resolve_ctx.new_page()
            resolve_page.set_extra_http_headers({"Authorization": f"Bearer {token}"})
            id_map = _resolve_ids(resolve_page, base_url)
            for key in list(id_map):
                if key not in pages:
                    del id_map[key]
            resolve_page.close()
            resolve_ctx.close()

        for page_key, route_template in list(pages.items()):
            route_path = route_template
            if page_key in id_map:
                route_path = route_template.format(id=id_map[page_key])

            page_results: dict[str, Any] = {}

            for vp_name, viewport in viewports.items():
                for theme in themes:
                    combo_key = f"{vp_name}-{theme}"
                    ctx = browser.new_context(viewport=viewport)
                    page = ctx.new_page()

                    # Set auth header if we have a token.
                    if token:
                        page.set_extra_http_headers({
                            "Authorization": f"Bearer {token}",
                        })

                    # Apply theme before navigation.
                    page.add_init_script(f"""
                        document.documentElement.setAttribute('data-theme', '{theme}');
                    """)

                    axe_result = _run_axe(page, page_key, route_path, base_url)

                    # Keyboard check (once per page, desktop viewport).
                    kb_result: dict[str, Any] | None = None
                    if vp_name == "desktop":
                        try:
                            page.keyboard.press("Tab")
                            kb_result = _check_keyboard(page)
                        except Exception:
                            kb_result = {"error": "keyboard check failed"}

                    # Reduced motion check (once per page, desktop viewport).
                    rm_result: dict[str, Any] | None = None
                    if vp_name == "desktop":
                        try:
                            rm_result = _check_reduced_motion(page)
                        except Exception:
                            rm_result = {"error": "reduced motion check failed"}

                    page_results[combo_key] = {
                        "axe": axe_result,
                        "keyboard": kb_result,
                        "reduced_motion": rm_result,
                    }

                    # Count for summary.
                    summary = results["summary"]
                    if "error" in axe_result:
                        summary["errors"] += 1
                    elif axe_result.get("violations"):
                        summary["failed"] += 1
                    else:
                        summary["passed"] += 1
                    summary["total_checks"] += 1

                    page.close()
                    ctx.close()

            results["pages"][page_key] = page_results

    finally:
        try:
            browser.close()
        except Exception:
            pass
        pw_instance.stop()

    return results


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------


def _check_gates(results: dict[str, Any]) -> list[str]:
    """Evaluate results against gates, return list of failure messages."""
    failures: list[str] = []

    for page_key, page_data in results.get("pages", {}).items():
        for combo_key, combo_data in page_data.items():
            axe = combo_data.get("axe", {})

            if "error" in axe:
                failures.append(
                    f"{page_key}/{combo_key}: page load error — {axe['error']}"
                )
                continue

            for v in axe.get("violations", []):
                impact = v.get("impact", "?")
                if impact in _FAIL_SEVERITIES:
                    failures.append(
                        f"{page_key}/{combo_key}: [{impact}] {v['id']} ({v.get('nodes', '?')} nodes) — {v.get('description', '')[:100]}"
                    )

            kb = combo_data.get("keyboard")
            if kb and isinstance(kb, dict) and kb.get("issues"):
                for issue in kb["issues"]:
                    failures.append(f"{page_key}/{combo_key}: keyboard — {issue}")

    return failures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run accessibility checks via axe-core + Playwright (with auth support)",
    )
    parser.add_argument(
        "--base-url",
        default=_env("ECDAT_A11Y_BASE_URL", "http://localhost:3000"),
        help="Base URL of the running dashboard (default: http://localhost:3000)",
    )
    parser.add_argument(
        "--pages",
        nargs="*",
        default=None,
        help="Pages to check (default: all)",
    )
    parser.add_argument(
        "--viewport",
        choices=list(VIEWPORTS.keys()),
        default=None,
        help="Viewport to test (default: both desktop and mobile)",
    )
    parser.add_argument(
        "--theme",
        choices=list(THEMES),
        default=None,
        help="Theme to test (default: both light and dark)",
    )
    parser.add_argument(
        "--skip-auth",
        action="store_true",
        help="Skip authenticated pages (no login attempt)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write detailed JSON report to this file",
    )
    parser.add_argument(
        "--install-browsers",
        action="store_true",
        help="Install Playwright browsers before running",
    )
    args = parser.parse_args()

    if args.install_browsers:
        _ensure_playwright_browsers()

    # Filter pages.
    pages = dict(ALL_PAGES)
    if args.pages:
        pages = {k: v for k, v in pages.items() if k in args.pages}
    if not pages:
        print(f"ERROR: no valid pages. Available: {list(ALL_PAGES.keys())}", file=sys.stderr)
        return 1

    # Filter viewports.
    viewports = dict(VIEWPORTS)
    if args.viewport:
        viewports = {args.viewport: VIEWPORTS[args.viewport]}

    # Filter themes.
    if args.theme:
        selected_themes: tuple[str, ...] = (str(args.theme),)
    else:
        selected_themes = THEMES

    # Resolve credentials.
    creds = _require_creds()
    if not args.skip_auth and not creds:
        print(
            "WARNING: ECDAT_A11Y_USERNAME / ECDAT_A11Y_PASSWORD not set. "
            "Skipping authenticated pages. Use --skip-auth to silence this.",
            file=sys.stderr,
        )
        args.skip_auth = True

    # Build effective page list.
    effective_pages = dict(pages)
    if args.skip_auth or not creds:
        for p in list(PROTECTED_PAGES) + list(ID_PAGES):
            effective_pages.pop(p, None)

    if not effective_pages:
        print("ERROR: no pages to check after auth filtering.", file=sys.stderr)
        return 1

    viewport_display = list(viewports.keys())
    theme_display = list(selected_themes)
    auth_display = "skipped" if (args.skip_auth or not creds) else "enabled"
    print(
        f"A11y check: base={args.base_url}, "
        f"pages={list(effective_pages.keys())}, "
        f"viewports={viewport_display}, themes={theme_display}, auth={auth_display}"
    )

    results = run_checks(
        base_url=args.base_url,
        pages=effective_pages,
        viewports=viewports,
        themes=selected_themes,
        skip_auth=args.skip_auth or not bool(creds),
        creds=creds,
    )

    # Write report.
    out_dir = _output_dir()
    report_name = f"a11y-{_timestamp()}.json"
    report_path = out_dir / report_name
    report_path.write_text(json.dumps(results, indent=2))
    print(f"\nReport written to {report_path}")

    # Also write a latest copy.
    latest = out_dir / "latest.json"
    latest.write_text(json.dumps(results, indent=2))

    # Evaluate gates.
    failures = _check_gates(results)
    if failures:
        print(f"\nFAILED: {len(failures)} serious/critical a11y violation(s)")
        for f in failures:
            print(f"  - {f}")
        return 1

    total = results["summary"]["total_checks"]
    passed = results["summary"]["passed"]
    print(f"\nPASSED: {passed}/{total} checks passed (no serious/critical violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
