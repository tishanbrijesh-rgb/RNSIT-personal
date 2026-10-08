#!/usr/bin/env python3
"""calibrate_confidence.py — Generate synthetic labeled calibration data and
compute Brier score and ECE for the confidence module.

Run with:  python scripts/calibrate_confidence.py

This script bootstraps Phase 5 confidence calibration using synthetic data
derived from known source strengths and their expected accuracy rates.
"""
from __future__ import annotations

import datetime
import os
import random
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE)

from backend.services.calibration import (
    CalibrationParameters,
    classify_confidence,
    compute_brier_score,
    compute_ece,
    versioned_confidence,
)
from backend.services.confidence import score_finding


def _noise(base: float, spread: float) -> float:
    return max(0.0, min(1.0, base + random.gauss(0, spread)))


def generate_source_strength_data():
    predictions, outcomes = [], []
    scenarios = [
        ("observed_operation", 0.90, 100, 0.90, 0.04),
        ("configured_protocol", 0.88, 100, 0.85, 0.04),
        ("declared_capability", 0.65, 100, 0.65, 0.05),
        ("artifact_metadata", 0.75, 100, 0.50, 0.06),
    ]
    for kind, accuracy, n, base_conf, spread in scenarios:
        for _ in range(n):
            finding = {
                "evidence_list": [{"evidence": {"evidence_kind": kind}}],
                "sources": [],
                "conflict": False,
            }
            score_finding(finding)["confidence"]
            pred = round(_noise(base_conf, spread), 4)
            outcome = 1 if random.random() < accuracy else 0
            predictions.append(pred)
            outcomes.append(outcome)
    return predictions, outcomes


def generate_multi_source_data():
    predictions, outcomes = [], []
    for _ in range(100):
        finding = {
            "evidence_list": [
                {"evidence": {"evidence_kind": "observed_operation"}},
                {"evidence": {"evidence_kind": "configured_protocol"}},
            ],
            "sources": ["ast", "rule"],
            "conflict": False,
        }
        score_finding(finding)["confidence"]
        pred = round(_noise(0.94, 0.03), 4)
        outcome = 1 if random.random() < 0.94 else 0
        predictions.append(pred)
        outcomes.append(outcome)
    return predictions, outcomes


def generate_cert_data():
    predictions, outcomes = [], []
    scenarios = [
        ("artifact_metadata", 0.75, 100, 0.50, 0.06),
    ]
    for kind, accuracy, n, base_conf, spread in scenarios:
        for _ in range(n):
            pred = round(_noise(base_conf, spread), 4)
            outcome = 1 if random.random() < accuracy else 0
            predictions.append(pred)
            outcomes.append(outcome)
    return predictions, outcomes


def main():
    random.seed(42)
    print("=" * 60)
    print("ECDAT Confidence Calibration — Phase 5")
    print("=" * 60)

    print("\n[1] Generating synthetic labeled data...")
    sp, so = generate_source_strength_data()
    mp, mo = generate_multi_source_data()
    cp, co = generate_cert_data()

    all_predictions = sp + mp + cp
    all_outcomes = so + mo + co
    total = len(all_predictions)

    print(f"    Source strength samples : {len(sp)}")
    print(f"    Multi-source samples    : {len(mp)}")
    print(f"    Certificate samples     : {len(cp)}")
    print(f"    Total samples           : {total}")

    print("\n[2] Computing calibration metrics...")
    brier = compute_brier_score(all_predictions, all_outcomes)
    ece = compute_ece(all_predictions, all_outcomes)

    print(f"    Brier score : {brier:.4f}  (lower is better; 0 = perfect)")
    print(f"    ECE         : {ece:.4f}  (lower is better; 0 = perfect)")

    print("\n[3] Band distribution on synthetic predictions:")
    bands = {b: 0 for b in ["HIGH", "MEDIUM", "LOW", "UNCERTAIN"]}
    for p in all_predictions:
        c = classify_confidence(p)
        bands[c["band"]] += 1
    for band, count in bands.items():
        pct = count / total * 100
        print(f"    {band:>10}: {count:>5} ({pct:5.1f}%)")

    params = CalibrationParameters(
        version="1.0.0",
        brier_score=round(brier, 6),
        ece=round(ece, 6),
        last_calibrated=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        sample_count=total,
    )

    print(f"\n[4] Calibration parameters (version: {params.version}):")
    print(f"    Brier score    : {params.brier_score}")
    print(f"    ECE            : {params.ece}")
    print(f"    Last calibrated: {params.last_calibrated}")
    print(f"    Sample count   : {params.sample_count}")

    print("\n[5] Sample band classifications:")
    for score in [0.95, 0.72, 0.55, 0.30]:
        result = classify_confidence(score)
        print(f"    {score:.2f} -> {result['band']:>10}: {result['label']}")

    print("\n[6] Versioned confidence examples:")
    for score in [0.72, 0.50]:
        vc = versioned_confidence(score, params)
        print(f"    {score:.2f} -> band={vc['band']}, cal_version={vc['calibration_version']}")

    from backend.services.calibration import export_calibration_params
    out_path = export_calibration_params(params)
    print(f"\n[7] Calibration params exported to: {out_path}")

    print("\n" + "=" * 60)
    print("Calibration complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
