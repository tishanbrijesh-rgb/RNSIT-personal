#!/usr/bin/env python3
"""
Schema validation for ECDAT scan results and CBOM exports.

Run as part of CI to catch accidental schema drift:

    python scripts/validate_schema.py              # validate last DB scan result
    python scripts/validate_schema.py --cbom        # validate CBOM export shape
    python scripts/validate_schema.py --scan-id 42  # validate a specific scan
    python scripts/validate_schema.py --cbom --file cbom.json  # validate file
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.db import SessionLocal
from backend.models.asset import CryptoAssetDB
from backend.models.scan_job import ScanJobDB

# ── asset schema ─────────────────────────────────────────────────────────────

_ASSET_REQUIRED_KEYS = {
    "scan_job_id": int,
    "algorithm": str,
    "category": str,
    "source": list,
    "location": str,
    "evidence_json": dict,
    "confidence": (int, float),
    "conflict": bool,
    "quantum_vulnerable": bool,
    "priority_score": int,
    "priority_label": str,
    "pqc_candidate": str,
    "business_criticality": str,
    "usage": str,
    "library": str,
    "protocol": str,
    "key_size": (int, type(None)),
    "data_sensitivity": str,
    "data_lifetime_years": int,
    "migration_time_years": int,
    "threat_horizon_years": int,
    "exposure": str,
    "migration_effort": str,
    "risk_reasons": list,
    "hybrid_recommended": bool,
    "logical_asset_id": str,
    "evidence_kind": str,
    "parser_version": str,
    "evidence_quality": str,
    "confirmed_use": bool,
    "capability_only": bool,
    "created_at": datetime,
}

_EVIDENCE_JSON_REQUIRED_KEYS = {
    "evidence_kind": str,
    "parser_version": str,
}

_PRIORITY_LABELS = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
_EXPOSURE_VALUES = {"isolated", "internal", "partner", "internet"}
_EFFORT_VALUES = {"low", "medium", "high", "critical"}
_BUSINESS_CRITICALITY_VALUES = {"low", "medium", "high", "critical"}


def _validate_asset(asset: CryptoAssetDB) -> list[str]:
    """Return validation errors for a single asset (empty == valid)."""
    errors: list[str] = []

    for key, expected_type in _ASSET_REQUIRED_KEYS.items():
        value = getattr(asset, key, None)
        allowed_types = expected_type if isinstance(expected_type, tuple) else (expected_type,)
        if value is None and type(None) not in allowed_types:
            errors.append(f"Asset {asset.id}: missing required field '{key}'")
            continue
        if value is not None and not isinstance(value, allowed_types):
            errors.append(
                f"Asset {asset.id}: field '{key}' expected {expected_type}, "
                f"got {type(value).__name__}"
            )

    if asset.priority_label not in _PRIORITY_LABELS:
        errors.append(
            f"Asset {asset.id}: priority_label '{asset.priority_label}' "
            f"not in {_PRIORITY_LABELS}"
        )
    if asset.exposure not in _EXPOSURE_VALUES:
        errors.append(
            f"Asset {asset.id}: exposure '{asset.exposure}' not in {_EXPOSURE_VALUES}"
        )
    if asset.migration_effort not in _EFFORT_VALUES:
        errors.append(
            f"Asset {asset.id}: migration_effort '{asset.migration_effort}' "
            f"not in {_EFFORT_VALUES}"
        )
    if asset.business_criticality not in _BUSINESS_CRITICALITY_VALUES:
        errors.append(
            f"Asset {asset.id}: business_criticality "
            f"'{asset.business_criticality}' not in {_BUSINESS_CRITICALITY_VALUES}"
        )
    if asset.quantum_vulnerable not in (True, False):
        errors.append(f"Asset {asset.id}: quantum_vulnerable must be bool")
    if asset.conflict not in (True, False):
        errors.append(f"Asset {asset.id}: conflict must be bool")
    if not (0.0 <= getattr(asset, "confidence", -1) <= 1.0):
        errors.append(
            f"Asset {asset.id}: confidence {asset.confidence} not in [0, 1]"
        )
    if not (0 <= getattr(asset, "priority_score", -1) <= 100):
        errors.append(
            f"Asset {asset.id}: priority_score {asset.priority_score} not in [0, 100]"
        )

    ej: dict[str, Any] = asset.evidence_json or {}  # type: ignore[assignment]
    for key in _EVIDENCE_JSON_REQUIRED_KEYS:
        if key not in ej:
            errors.append(f"Asset {asset.id}: evidence_json missing '{key}'")

    return errors


def validate_scan(scan_id: int) -> tuple[bool, list[str]]:
    """Validate all assets for a given scan job."""
    db = SessionLocal()
    try:
        job = db.get(ScanJobDB, scan_id)
        if job is None:
            return False, [f"Scan job {scan_id} not found"]

        assets = (
            db.query(CryptoAssetDB)
            .filter(CryptoAssetDB.scan_job_id == scan_id)
            .all()
        )
        if not assets:
            return True, [f"INFO: scan {scan_id} has no assets (status={job.status})"]

        errors: list[str] = []
        for asset in assets:
            errors.extend(_validate_asset(asset))
        return (len(errors) == 0), errors
    finally:
        db.close()


def validate_scans_from_db(scan_id: int | None = None) -> int:
    """Validate scan(s) from database.  Returns 0 on success, 1 on failure."""
    db = SessionLocal()
    try:
        if scan_id is not None:
            scan_ids = [scan_id]
        else:
            completed = (
                db.query(ScanJobDB)
                .filter(ScanJobDB.status == "completed")  # type: ignore[arg-type]
                .order_by(ScanJobDB.id.desc())  # type: ignore[attr-defined]
                .limit(5)
                .all()
            )
            scan_ids = [j.id for j in completed]
            if not scan_ids:
                print("VALIDATION FAILED: no completed scans found")
                return 1
    finally:
        db.close()

    all_errors: list[str] = []
    for sid in scan_ids:
        ok, errors = validate_scan(sid)
        if errors:
            if ok:
                print(f"  scan {sid}: OK ({len(errors)} notes)")
            else:
                all_errors.extend(errors)
        else:
            print(f"  scan {sid}: OK")

    if all_errors:
        print(f"\nVALIDATION FAILED: {len(all_errors)} error(s)")
        for e in all_errors:
            print(f"  - {e}")
        return 1

    print(f"All {len(scan_ids)} scan(s) passed schema validation")
    return 0


# ── CBOM schema ──────────────────────────────────────────────────────────────

_CBOM_REQUIRED_TOP = {"bomFormat", "specVersion", "version", "metadata", "components"}
_COMPONENT_REQUIRED = {"type", "name"}
_PROPERTY_REQUIRED = {"name", "value"}


def _validate_cbom(data: dict[str, Any]) -> list[str]:
    """Return validation errors for a CBOM (CycloneDX) dict."""
    errors: list[str] = []

    for key in _CBOM_REQUIRED_TOP:
        if key not in data:
            errors.append(f"CBOM missing top-level key '{key}'")

    if not isinstance(data.get("components"), list):
        errors.append("CBOM 'components' must be a list")
        return errors

    for idx, component in enumerate(data["components"]):
        for key in _COMPONENT_REQUIRED:
            if key not in component:
                errors.append(f"CBOM component[{idx}] missing '{key}'")
        for pidx, prop in enumerate(component.get("properties", [])):
            for key in _PROPERTY_REQUIRED:
                if key not in prop:
                    errors.append(
                        f"CBOM component[{idx}].property[{pidx}] missing '{key}'"
                    )
    return errors


def validate_cbom_file(filepath: str, scan_id: int | None = None) -> int:
    """Validate a CBOM JSON file or fetch from the API."""
    if filepath:
        data = json.loads(Path(filepath).read_text())
    else:
        from fastapi.testclient import TestClient

        from backend.main import app
        client = TestClient(app)
        url = "/api/cbom" + (f"?scan_id={scan_id}" if scan_id else "")
        resp = client.get(url)
        if resp.status_code != 200:
            print(f"CBOM endpoint returned {resp.status_code}: {resp.text[:200]}")
            return 1
        data = resp.json()

    errors = _validate_cbom(data)
    if errors:
        print(f"CBOM VALIDATION FAILED ({len(errors)} error(s)):")
        for e in errors:
            print(f"  - {e}")
        return 1

    comp_count = len(data.get("components", []))
    print(f"CBOM valid: {comp_count} component(s)")
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate ECDAT scan results against expected schemas",
    )
    parser.add_argument(
        "--scan-id", type=int, default=None,
        help="Validate a specific scan job (default: most recent completed scan)",
    )
    parser.add_argument(
        "--cbom", action="store_true",
        help="Validate CBOM export shape instead of scan assets",
    )
    parser.add_argument(
        "--file", default=None,
        help="JSON file to validate (used with --cbom)",
    )
    args = parser.parse_args()

    if args.cbom:
        return validate_cbom_file(args.file, args.scan_id)

    return validate_scans_from_db(args.scan_id)


if __name__ == "__main__":
    sys.exit(main())
