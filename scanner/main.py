#!/usr/bin/env python3
"""
Scanner entrypoint — orchestrates all collectors, walks a repository, and
returns a structured evidence map suitable for ingestion by the correlator.

Usage:
    from scanner.main import scan
    evidence = scan("/path/to/repo")
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from scanner.collectors.registry import CollectorRegistry
from scanner.limits import (
    check_memory_budget,
    max_evidence_count,
    max_file_bytes,
    positive_int,
    scan_duration_budget_ms,
    scan_memory_budget_mb,
)
from scanner.redaction import redact_evidence

# Ensure `scanner` package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging as _logging

_scanner_logger = _logging.getLogger("ecdat.scanner")


def _print(msg: str) -> None:
    """Log progress message via structured logger."""
    _scanner_logger.info(msg)


def _safe_filename(repo_path: str) -> str:
    """Display-friendly repo path."""
    return os.path.basename(repo_path.rstrip("/\\")) or repo_path


def _scan_profile() -> str:
    """Return the declared inventory profile, failing closed on typos."""
    profile = os.getenv("ECDAT_SCAN_PROFILE", "source").strip().lower()
    if profile not in {"source", "environment"}:
        raise ValueError("Invalid ECDAT_SCAN_PROFILE")
    return profile


def _inventory(
    repo_path: str,
    profile: str | None = None,
    registry: CollectorRegistry | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[str], list[str], dict[str, str]]:
    """Return all files, supported files, and fixed failure codes by path."""
    registry = registry or CollectorRegistry()
    all_files: list[str] = []
    supported: list[str] = []
    failed: dict[str, str] = {}
    file_limit = positive_int('ECDAT_MAX_SCAN_FILES', 100000, 1000000)
    byte_limit = max_file_bytes()
    excluded = {
        ".git", "node_modules", "dist", "build", "__pycache__",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".runtime",
    }
    if (profile or _scan_profile()) == "source":
        excluded.update({".venv", "venv", "env"})
    last_progress = time.perf_counter()

    def report() -> None:
        if progress_callback is not None:
            progress_callback({
                "_phase": "indexing",
                "_files_discovered": len(all_files),
                "_files_supported": len(supported),
                "_files_processed": 0,
                "_files_total": len(supported),
            })

    def inventory_error(_error: OSError) -> None:
        # Do not leak inaccessible absolute paths or platform error details.
        raise OSError("Unable to inventory repository tree") from None

    for dirpath, dirnames, filenames in os.walk(repo_path, onerror=inventory_error):
        dirnames[:] = [d for d in dirnames if d not in excluded
                      and not os.path.islink(os.path.join(dirpath, d))
                      and not getattr(os.path, 'isjunction', lambda p: False)(os.path.join(dirpath, d))]
        for filename in filenames:
            path = os.path.join(dirpath, filename)
            all_files.append(path)
            if len(all_files) > file_limit:
                raise ValueError('Repository exceeds configured file count limit')
            ext = os.path.splitext(filename)[1].lower()
            if ext in registry.supported_extensions or filename in registry.supported_filenames:
                supported.append(path)
                try:
                    if os.path.islink(path):
                        failed[path] = "linked_file"
                        continue
                    if os.path.getsize(path) > byte_limit:
                        failed[path] = "oversized"
                        continue
                    with open(path, "rb") as stream:
                        stream.read(1)
                except OSError:
                    failed[path] = "unreadable"
        now = time.perf_counter()
        if now - last_progress >= 0.5:
            report()
            last_progress = now
    report()
    return all_files, supported, failed


def _relative_failure_path(repo_path: str, path: str) -> str | None:
    """Return an API-safe, in-root relative path without resolving symlinks."""
    try:
        relative = os.path.relpath(os.path.abspath(path), os.path.abspath(repo_path))
    except ValueError:
        return None
    parts = relative.split(os.sep)
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    # Encode separators, drive-like colons, controls and percent signs that are
    # unsafe or ambiguous in the public failure schema while retaining identity.
    return "/".join(quote(part, safe="-._~ ") for part in parts)


def _budget_failure(started: float, duration_budget: int) -> str | None:
    if duration_budget > 0 and (time.perf_counter() - started) * 1000 >= duration_budget:
        return "duration_budget_exceeded"
    if check_memory_budget():
        return "memory_budget_exceeded"
    return None


def _collect_path(
    path: str,
    registry: CollectorRegistry,
    combined: dict,
    collector_stats: dict[str, int],
    failed_paths: set[str],
    failure_codes: dict[str, str],
    evidence_count: int,
    evidence_limit: int,
) -> int:
    for name, handler in registry.handlers_for(path):
        def record_failure(failed_path: str, collector: str = name) -> None:
            failed_paths.add(failed_path)
            failure_codes.setdefault(
                failed_path,
                "certificate_error" if collector == "cert" else "parse_error",
            )

        try:
            assets = handler(path, on_error=record_failure)
        except Exception:
            # Repository-controlled input must never terminate the complete
            # scan. Record a bounded failure code and continue with the next
            # collector/file without exposing source paths or parser details.
            failed_paths.add(path)
            failure_codes.setdefault(path, "parse_error")
            continue
        if evidence_count + len(assets) > evidence_limit:
            raise ValueError("Scan exceeds configured evidence count limit")
        for asset in assets:
            item = asset.to_dict()
            item["evidence"] = redact_evidence(item.get("evidence", {}))
            combined.setdefault((asset.algorithm, asset.location), []).append(item)
            collector_stats[name] += 1
            evidence_count += 1
    return evidence_count


def _collect_supported_evidence(
    supported: list[str],
    files_discovered: int,
    failure_codes: dict[str, str],
    registry: CollectorRegistry,
    started: float,
    duration_budget: int,
    evidence_limit: int,
    progress_callback: Callable[[dict[str, Any]], None] | None,
) -> tuple[dict, dict[str, int], set[str]]:
    combined: dict[tuple[str, str], list[dict[str, Any]]] = {}
    collector_stats = dict.fromkeys(("ast", "rule", "dep", "cert"), 0)
    failed_paths = set(failure_codes)
    evidence_count = 0
    last_progress = started

    def report(processed: int) -> None:
        if progress_callback is not None:
            progress_callback({**collector_stats, "_phase": "collecting",
                               "_files_discovered": files_discovered,
                               "_files_supported": len(supported),
                               "_files_processed": processed,
                               "_files_total": len(supported)})

    report(0)
    for index, path in enumerate(sorted(supported), 1):
        if path not in failed_paths:
            budget_failure = _budget_failure(started, duration_budget)
            if budget_failure:
                failure_codes[path] = budget_failure
                failed_paths.add(path)
                continue
            evidence_count = _collect_path(
                path, registry, combined, collector_stats, failed_paths,
                failure_codes, evidence_count, evidence_limit,
            )
        now = time.perf_counter()
        if now - last_progress >= 1.0 or index == len(supported):
            report(index)
            last_progress = now
    return combined, collector_stats, failed_paths


def _blind_spots(profile: str, supported: list[str], failed_paths: set[str], duration_budget: int) -> list[str]:
    spots = [
        "Runtime-generated cryptography is outside static scan scope",
        "Compiled binaries and obfuscated bytecode require binary analysis",
        "Container images, cloud services, network traffic and HSMs are not inspected",
        ("Source profile excludes .git, node_modules, dist, build, __pycache__, .venv, venv and env directories"
         if profile == "source" else
         "Environment profile excludes .git, node_modules, dist, build and __pycache__ directories"),
        "Coverage measures files processed without reported collector errors, not detection completeness",
        "Linked directories/files are excluded; oversized files count as processing failures",
    ]
    if not supported:
        spots.append("No supported files were found; coverage is not established")
    if failed_paths:
        spots.append(f"{len(failed_paths)} supported file(s) had read or parser errors; evidence may be partial")
    if duration_budget > 0:
        spots.append(f"Scan duration capped at {duration_budget / 1000:.0f}s; remaining files skipped on budget exhaustion")
    if scan_memory_budget_mb() > 0:
        spots.append(f"Memory budget of {scan_memory_budget_mb()} MB enforced; remaining files skipped on budget exhaustion")
    return spots


def _failure_payload(repo_path: str, failed_paths: set[str], failure_codes: dict[str, str]) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for path in sorted(failed_paths):
        relative = _relative_failure_path(repo_path, path)
        if relative is not None:
            failures.append({"path": relative, "reason": failure_codes.get(path, "parse_error")})
    return failures


def scan_with_metrics(
    repo_path: str, progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], dict[str, Any]]:
    """Run every collector and return evidence plus measured scope/coverage metrics."""
    started = time.perf_counter()
    if not os.path.isdir(repo_path):
        raise ValueError(f"Repository path does not exist: {repo_path}")
    profile = _scan_profile()
    evidence_limit = max_evidence_count()
    registry = CollectorRegistry()
    all_files, supported, failure_codes = _inventory(
        repo_path, profile, registry, progress_callback,
    )

    _print("=== ECDAT discovery-assurance scan starting ===")
    _print(f"Target: {_safe_filename(repo_path)} ({repo_path})")
    duration_budget = scan_duration_budget_ms()
    mem_budget_code = check_memory_budget()
    if mem_budget_code:
        failure_codes[repo_path] = mem_budget_code
    combined, collector_stats, failed_paths = _collect_supported_evidence(
        supported, len(all_files), failure_codes, registry, started, duration_budget,
        evidence_limit, progress_callback,
    )
    scanned = max(0, len(supported) - len(failed_paths))
    coverage = round(scanned / len(supported) * 100, 2) if supported else 0.0
    blind_spots = _blind_spots(profile, supported, failed_paths, duration_budget)
    failures = _failure_payload(repo_path, failed_paths, failure_codes)
    metrics = {
        "total_files": len(all_files), "in_scope_files": len(supported),
        "scanned_files": scanned, "failed_files": len(failed_paths),
        "coverage_pct": coverage, "collector_stats": collector_stats,
        "blind_spots": blind_spots,
        "failures": failures,
        "duration_ms": round((time.perf_counter() - started) * 1000),
    }
    total = sum(len(value) for value in combined.values())
    _print(f"=== Scan complete: {total} evidences, {coverage:.1f}% supported-file coverage ===")
    return combined, metrics


def scan(repo_path: str) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """
    Walk a repository and discover cryptographic assets using all collectors.

    Returns:
        dict keyed by (algorithm, filepath) -> list of evidence dicts. Each
        evidence dict has shape:
            {
              "algorithm": str,
              "category": str,
              "source": "ast" | "dep" | "cert",
              "location": str,
              "evidence": dict,
              "confidence": float,
              ...
            }
    """
    try:
        evidence, _metrics = scan_with_metrics(repo_path)
        return evidence
    except ValueError as exc:
        _print(f"ERROR: {exc}")
        return {}


def scan_to_dict(repo_path: str) -> list[dict[str, Any]]:
    """Return evidence as a list of dicts (convenience for downstream code)."""
    grouped = scan(repo_path)
    flat: list[dict[str, Any]] = []
    for evs in grouped.values():
        flat.extend(evs)
    return flat


def _cli_main() -> None:
    parser = argparse.ArgumentParser(description="ECDAT scanner")
    parser.add_argument("repo_path", help="Path to repository or directory")
    parser.add_argument("--output", "-o", default=None,
                        help="Write evidence JSON to this file")
    args = parser.parse_args()

    start = time.time()
    evidences = scan_to_dict(args.repo_path)
    elapsed = time.time() - start

    _print(f"Elapsed: {elapsed:.2f}s")

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".",
                    exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(evidences, fh, indent=2)
        _print(f"Wrote {args.output}")
    else:
        _scanner_logger.info("Evidence sample", extra={"extra_data": {"count": min(3, len(evidences)), "sample": evidences[:3]}})


if __name__ == "__main__":
    _cli_main()
