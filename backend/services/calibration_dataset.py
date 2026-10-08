"""Empirical calibration dataset � labeled confidence-outcome pairs.

Phase 4 (ImpactX): provides ground-truth labels for confidence calibration,
per-band Brier/ECE breakdown, and drift detection against the shipped
calibration_params.json baseline.

Typical flow:
    ds = CalibrationDataset()
    ds.load()                           # read disk
    ds.add_records(scan_results)        # accumulate from live scans
    ds.save()                           # persist augmented dataset

    metrics = ds.compute_metrics()      # overall + per-band
    drift   = ds.drift_report()         # compare to shipped params
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.services.calibration import (
    CalibrationParameters,
    compute_brier_score,
    compute_ece,
    load_calibration_params,
)

_DEFAULT_PATH = os.path.join(
    os.path.dirname(__file__), "calibration_dataset.json"
)


@dataclass
class CalibrationRecord:
    """One labeled confidence-outcome pair."""
    confidence: float       # 0-1 predicted confidence
    outcome: int             # 1 = correct, 0 = incorrect
    algorithm: str           # e.g. "RSA", "AES"
    source: str              # collector name (ast, dep, cert, rule)
    evidence_kind: str       # observed_operation | declared_capability | ...
    band: str                # HIGH | MEDIUM | LOW | UNCERTAIN
    context_hash: str = ""   # hash of file+line for dedup


@dataclass
class CalibrationDataset:
    """In-memory store of labeled calibration records."""

    records: list[CalibrationRecord] = field(default_factory=list)
    path: str = _DEFAULT_PATH

    # -- persistence

    def load(self, path: str | None = None) -> None:
        p = path or self.path
        if not os.path.exists(p):
            return
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        self.records = [
            CalibrationRecord(**r) for r in raw.get("records", [])
        ]
        if path:
            self.path = path

    def save(self, path: str | None = None) -> str:
        p = path or self.path
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        payload = {
            "version": "1.0.0",
            "count": len(self.records),
            "records": [asdict(r) for r in self.records],
        }
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        return p

    # -- mutation

    def add_records(self, records: list[CalibrationRecord]) -> int:
        seen: set[tuple[str, str, str]] = {
            (r.context_hash, r.algorithm, r.source) for r in self.records
        }
        new: list[CalibrationRecord] = []
        for r in records:
            key = (r.context_hash, r.algorithm, r.source)
            if key not in seen:
                seen.add(key)
                new.append(r)
        self.records.extend(new)
        return len(new)

    def add_from_scan(
        self,
        assets: list[dict[str, Any]],
        outcome_fn = None,
    ) -> int:
        if outcome_fn is None:
            def outcome_fn(a):
                return 1 if a.get("confirmed_use") else 0
        import hashlib

        from backend.services.calibration import classify_confidence
        records = []
        for asset in assets:
            conf = float(asset.get("confidence", 0.0))
            band = classify_confidence(conf)["band"]
            context = (
                asset.get("location", "")
                + ":"
                + str(asset.get("evidence_json", {}).get("operation_anchor", ""))
            )
            ctx_hash = hashlib.sha256(context.encode()).hexdigest()[:16]
            records.append(CalibrationRecord(
                confidence=conf,
                outcome=int(outcome_fn(asset)),
                algorithm=str(asset.get("algorithm", "")),
                source=str(asset.get("source", [""])[0] if asset.get("source") else ""),
                evidence_kind=str(asset.get("evidence_kind", "")),
                band=band,
                context_hash=ctx_hash,
            ))
        return self.add_records(records)

    # -- metrics

    def compute_metrics(
        self,
        params: CalibrationParameters | None = None,
    ) -> dict[str, Any]:
        params = params or load_calibration_params()
        from backend.services.calibration import CalibrationBands
        bands_order = CalibrationBands.ordered()
        by_band = {b: [] for b in bands_order}
        for r in self.records:
            by_band.setdefault(r.band, []).append(r)
        overall = _metrics_for(self.records)
        per_band = {}
        for band, recs in by_band.items():
            if not recs:
                per_band[band] = {"count": 0}
            else:
                per_band[band] = {"count": len(recs), **_metrics_for(recs)}
        return {
            "overall": overall,
            "per_band": per_band,
            "sample_count": len(self.records),
            "calibration_version": params.version,
        }

    def drift_report(
        self,
        params: CalibrationParameters | None = None,
    ) -> dict[str, Any]:
        params = params or load_calibration_params()
        current = self.compute_metrics(params)
        drift = {"version": params.version, "checks": []}
        for band, info in current["per_band"].items():
            if info.get("count", 0) < 10:
                continue
            actual = info.get("brier_score", 0.0)
            baseline = params.brier_score or 0.0
            threshold = 0.05
            delta = actual - baseline
            status = "OK" if abs(delta) <= threshold else "DRIFT"
            drift["checks"].append({
                "band": band,
                "baseline_brier": round(baseline, 4),
                "actual_brier": round(actual, 4),
                "delta": round(delta, 4),
                "threshold": threshold,
                "status": status,
                "count": info["count"],
            })
        any_drift = any(c["status"] == "DRIFT" for c in drift["checks"])
        drift["overall_status"] = "DRIFT_DETECTED" if any_drift else "OK"
        return drift

    def count(self) -> int:
        return len(self.records)

    def clear(self) -> None:
        self.records.clear()

    def __len__(self) -> int:
        return len(self.records)


def _metrics_for(records):
    if not records:
        return {"brier_score": None, "ece": None}
    preds = [r.confidence for r in records]
    outcomes = [r.outcome for r in records]
    brier = compute_brier_score(preds, outcomes)
    ece = compute_ece(preds, outcomes)
    return {
        "brier_score": round(brier, 6),
        "ece": round(ece, 6),
        "accuracy": round(sum(outcomes) / len(outcomes), 4),
    }
