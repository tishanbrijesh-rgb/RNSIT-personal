"""
Scanner runner — orchestrates the full scan pipeline in the supervised worker
(or synchronously for direct callers): scan -> correlate -> confidence -> risk -> persist.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any

# Ensure the scanner package is importable (it is volume-mounted at /scanner)
_SCANNER_ROOT = os.getenv("SCANNER_PATH", "/scanner")
if _SCANNER_ROOT not in sys.path:
    sys.path.insert(0, _SCANNER_ROOT)

from backend.logging_config import get_logger
from scanner.main import scan_with_metrics

logger = get_logger("ecdat.scanner_runner")

from backend.db import SessionLocal
from backend.models.asset import CryptoAssetDB
from backend.models.scan_failure import ScanFailureDB
from backend.models.scan_job import ScanJobDB
from backend.services.confidence import score_finding
from backend.services.correlator import correlate, correlate_v3
from backend.services.risk_engine import assess_risk
from scanner.limits import positive_int
from scanner.redaction import redact_evidence


def _apply_risk_defaults(finding: dict[str, Any]) -> dict[str, Any]:
    """Apply explicit policy defaults without overriding discovered context."""
    configured = dict(finding)
    configured.update({
        "business_criticality": finding.get("business_criticality", "medium"),
        "data_sensitivity": finding.get("data_sensitivity", "medium"),
        "data_lifetime_years": finding.get("data_lifetime_years", 10),
        "migration_time_years": finding.get("migration_time_years", 3),
        "threat_horizon_years": finding.get(
            "threat_horizon_years",
            positive_int("ECDAT_DEFAULT_THREAT_HORIZON_YEARS", 15, 100),
        ),
        "exposure": finding.get("exposure", "internal"),
        "migration_effort": finding.get("migration_effort", "medium"),
    })
    return configured


def _finding_provenance(finding: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Return collector parser versions and their original confidence reasons."""
    versions: list[str] = []
    reasons: list[dict[str, Any]] = []
    for item in finding.get("evidence_list") or []:
        version = item.get("parser_version")
        if version and version not in versions:
            versions.append(str(version))
        for reason in item.get("confidence_reasons") or []:
            if isinstance(reason, dict):
                reasons.append(dict(reason))
    return ",".join(versions), reasons


def run_scan(repo_path: str, scan_id: int | None = None) -> dict[str, Any]:
    """Ensure failures in correlation or persistence also terminate the job."""
    if scan_id is None:
        with SessionLocal() as db:
            job = ScanJobDB(repo_path=repo_path, status="queued")
            db.add(job)
            db.commit()
            db.refresh(job)
            scan_id = job.id
    with SessionLocal() as db:
        job = db.get(ScanJobDB, scan_id)
        if job is not None and job.status in {"pending", "queued"}:
            job.started_at = datetime.now(timezone.utc)
            job.status = "running"
            db.commit()
    try:
        return _run_scan(repo_path, scan_id)
    except Exception as exc:
        with SessionLocal() as db:
            job = db.query(ScanJobDB).filter(ScanJobDB.id == scan_id).first()
            if job is None:
                raise
            job.status = "failed"
            job.finished_at = datetime.now(timezone.utc)
            job.blind_spots = [f"Scan failed during processing ({type(exc).__name__}); results are incomplete"]
            db.commit()
        return {"scan_id": scan_id, "status": "failed", "assets_found": 0, "avg_confidence": 0.0}


def collect_scan_result(
    repo_path: str,
    progress_callback=None,
) -> dict[str, Any]:
    """Run repository-controlled parsing without touching the control-plane DB."""
    evidence, metrics = scan_with_metrics(repo_path, progress_callback=progress_callback)
    if progress_callback is not None:
        progress_callback({**metrics["collector_stats"], "_phase": "correlating",
                           "_files_discovered": metrics["total_files"],
                           "_files_supported": metrics["in_scope_files"],
                           "_files_processed": metrics["in_scope_files"]})
    correlator_version = os.getenv("ECDAT_CORRELATOR_VERSION", "v2")
    findings = correlate_v3(evidence) if correlator_version == "v3" else correlate(evidence)
    if progress_callback is not None:
        progress_callback({**metrics["collector_stats"], "_phase": "persisting",
                           "_files_discovered": metrics["total_files"],
                           "_files_supported": metrics["in_scope_files"],
                           "_files_processed": metrics["in_scope_files"],
                           "_findings_count": len(findings)})
    logger.info(
        "Correlation complete",
        extra={"extra_data": {
            "findings": len(findings),
            "correlator_version": correlator_version,
        }},
    )
    return {"findings": findings, "metrics": metrics}


def persist_scan_result(
    scan_id: int,
    scan_result: dict[str, Any],
    *,
    result_version: int = 1,
    session_factory=SessionLocal,
) -> dict[str, Any]:
    """Validate and persist a bounded worker artifact in the control plane."""
    findings = scan_result.get("findings")
    metrics = scan_result.get("metrics")
    if not isinstance(findings, list) or not isinstance(metrics, dict):
        raise ValueError("Invalid scan worker result")

    db = session_factory()
    persisted = 0
    confidences: list[float] = []
    try:
        job = (
            db.query(ScanJobDB)
            .filter(ScanJobDB.id == scan_id)
            .with_for_update()
            .one_or_none()
        )
        if job is None:
            raise ValueError("Scan job does not exist")
        if job.result_version >= result_version:
            return {
                "scan_id": scan_id,
                "status": job.status,
                "assets_found": job.assets_found,
                "avg_confidence": job.avg_confidence or 0.0,
                "coverage_pct": job.coverage_pct,
                "collector_stats": job.collector_stats or {},
            }
        if job.status not in {"pending", "queued", "running"}:
            raise ValueError("Scan job is not active")
        job.status = "running"
        db.commit()

        for finding in findings:
            if not isinstance(finding, dict):
                raise ValueError("Invalid finding in scan worker result")
            f = dict(finding)
            sc = score_finding(f)
            f["confidence"] = sc["confidence"]
            confidences.append(sc["confidence"])
            original_fields = set(f.keys())
            f = _apply_risk_defaults(f)
            risk = assess_risk(f, user_provided_fields=original_fields)
            f.update(risk)
            parser_version, confidence_reasons = _finding_provenance(f)
            evidence_list = f.get("evidence_list", [])

            db.add(CryptoAssetDB(
                scan_job_id=scan_id,
                algorithm=f.get("algorithm", ""),
                category=f.get("category", ""),
                source=list(f.get("sources", [])),
                location=f.get("location", ""),
                evidence_json={
                    "component": f.get("component", "repository-root"),
                    "evidence_kind": f.get("evidence_kind", "unknown"),
                    "parser_version": parser_version,
                    "confidence_by_source": f.get("confidence_by_source", {}),
                    "evidence_list": [
                        {**item, "evidence": redact_evidence(item.get("evidence", {}))}
                        for item in evidence_list
                    ],
                    "reasons": sc.get("reasons", []),
                    "conflicting_operations": f.get("conflicting_operations", []),
                    "context_conflicts": f.get("context_conflicts", {}),
                    "operation_anchor": f.get("operation_anchor", ""),
                    "correlation_version": f.get("correlation_version", ""),
                    "span_anchor": f.get("span_anchor", ""),
                    "linked_evidence": f.get("linked_evidence", []),
                    "ambiguous": f.get("ambiguous", False),
                    "ambiguous_count": f.get("ambiguous_count", 0),
                    "confirmed_use": risk.get("confirmed_use", False),
                    "capability_only": risk.get("capability_only", False),
                },
                confidence=sc["confidence"],
                conflict=f.get("conflict", False),
                quantum_vulnerable=risk["quantum_vulnerable"],
                priority_score=risk["priority_score"],
                priority_label=risk["priority_label"],
                pqc_candidate=risk["pqc_candidate"],
                business_criticality=f.get("business_criticality", "medium"),
                usage=f.get("usage", "unknown"),
                library=f.get("library", ""),
                protocol=f.get("protocol", ""),
                key_size=f.get("key_size"),
                data_sensitivity=f.get("data_sensitivity", "medium"),
                data_lifetime_years=f.get("data_lifetime_years", 10),
                migration_time_years=f.get("migration_time_years", 3),
                threat_horizon_years=f.get("threat_horizon_years", 15),
                exposure=f.get("exposure", "internal"),
                migration_effort=f.get("migration_effort", "medium"),
                risk_reasons=risk["risk_reasons"],
                hybrid_recommended=risk["hybrid_recommended"],
                logical_asset_id=f.get("logical_asset_id", ""),
                evidence_kind=f.get("evidence_kind", "unknown"),
                parser_version=parser_version,
                evidence_quality=str(risk.get("confidence_band", "UNCERTAIN")).lower(),
                confirmed_use=risk.get("confirmed_use", False),
                capability_only=risk.get("capability_only", False),
                risk_context_provenance=risk.get("risk_context_provenance", {}),
                span=evidence_list[0].get("span") or {} if evidence_list else {},
                confidence_reasons=confidence_reasons,
            ))
            persisted += 1

        avg_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
        job.finished_at = datetime.now(timezone.utc)
        job.status = "completed"
        job.result_version = result_version
        job.assets_found = persisted
        job.avg_confidence = avg_confidence
        job.total_files = int(metrics.get("total_files", 0))
        job.in_scope_files = int(metrics.get("in_scope_files", 0))
        job.scanned_files = int(metrics.get("scanned_files", 0))
        job.failed_files = int(metrics.get("failed_files", 0))
        job.coverage_pct = float(metrics.get("coverage_pct", 0.0))
        if job.started_at is not None:
            started = job.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            job.duration_ms = max(0, round((job.finished_at - started).total_seconds() * 1000))
        else:
            job.duration_ms = int(metrics.get("duration_ms", 0))
        job.collector_stats = metrics.get("collector_stats", {})
        job.blind_spots = list(metrics.get("blind_spots", []))
        for failure in metrics.get("failures", []):
            if isinstance(failure, dict):
                db.add(ScanFailureDB(
                    scan_job_id=scan_id,
                    path=str(failure.get("path", "")),
                    reason=str(failure.get("reason", "unknown")),
                ))
        db.commit()
        return {
            "scan_id": scan_id,
            "status": "completed",
            "assets_found": persisted,
            "avg_confidence": avg_confidence,
            "coverage_pct": job.coverage_pct,
            "collector_stats": job.collector_stats,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _run_scan(repo_path: str, scan_id: int | None = None) -> dict[str, Any]:
    """
    Execute the full scan pipeline and persist results.

    Returns a summary dict:
        {
          "scan_id": int,
          "status": "completed",
          "assets_found": int,
          "avg_confidence": float,
        }
    """
    # 1. Create or claim a queued job
    db = SessionLocal()
    try:
        if scan_id is None:
            job = ScanJobDB(repo_path=repo_path, status="running")
            db.add(job)
        else:
            job = db.query(ScanJobDB).filter(ScanJobDB.id == scan_id).first()
            if job is None:
                raise ValueError(f"Scan job {scan_id} does not exist")
            job.status = "running"
        db.commit()
        db.refresh(job)
        scan_id = job.id
    finally:
        db.close()

    logger.info("Starting scan", extra={"extra_data": {"scan_id": scan_id, "repo_path": repo_path}})

    def progress(stats: dict[str, int]) -> None:
        with SessionLocal() as progress_db:
            current = progress_db.get(ScanJobDB, scan_id)
            current.collector_stats = stats
            progress_db.commit()

    # The outer wrapper finalizes failures from every pipeline stage.
    evidence, metrics = scan_with_metrics(repo_path, progress_callback=progress)

    # 3. Correlate (v3 when ECDAT_CORRELATOR_VERSION=v3, else v2)
    _correlator_version = os.getenv("ECDAT_CORRELATOR_VERSION", "v2")
    if _correlator_version == "v3":
        findings = correlate_v3(evidence)
    else:
        findings = correlate(evidence)
    logger.info(
        "Correlation complete",
        extra={"extra_data": {"scan_id": scan_id, "findings": len(findings),
                               "correlator_version": _correlator_version}},
    )

    # 4. Score & risk-assess each finding, persist
    db = SessionLocal()
    persisted = 0
    confidences: list[float] = []
    try:
        for f in findings:
            f = dict(f)  # shallow copy
            sc = score_finding(f)
            f["confidence"] = sc["confidence"]
            confidences.append(sc["confidence"])
            # Risk context: use per-finding values when available,
            # fall back to policy defaults for unknown contexts.
            # Capture which fields were originally present so assess_risk can
            # label each input as user-provided or policy-default.
            _original_fields = set(f.keys())
            f = _apply_risk_defaults(f)
            risk = assess_risk(f, user_provided_fields=_original_fields)
            f.update(risk)
            parser_version, confidence_reasons = _finding_provenance(f)

            asset = CryptoAssetDB(
                scan_job_id=scan_id,
                algorithm=f.get("algorithm", ""),
                category=f.get("category", ""),
                source=list(f.get("sources", [])),
                location=f.get("location", ""),
                evidence_json={
                    "component": f.get("component", "repository-root"),
                    "evidence_kind": f.get("evidence_kind", "unknown"),
                    "parser_version": parser_version,
                    "confidence_by_source": f.get("confidence_by_source", {}),
                    "evidence_list": [{**item, "evidence": redact_evidence(item.get("evidence", {}))}
                                      for item in f.get("evidence_list", [])],
                    "reasons": sc.get("reasons", []),
                    "conflicting_operations": f.get("conflicting_operations", []),
                    "context_conflicts": f.get("context_conflicts", {}),
                    "operation_anchor": f.get("operation_anchor", ""),
                    "correlation_version": f.get("correlation_version", ""),
                    "span_anchor": f.get("span_anchor", ""),
                    "linked_evidence": f.get("linked_evidence", []),
                    "ambiguous": f.get("ambiguous", False),
                    "ambiguous_count": f.get("ambiguous_count", 0),
                    "confirmed_use": risk.get("confirmed_use", False),
                    "capability_only": risk.get("capability_only", False),
                },
                confidence=sc["confidence"],
                conflict=f.get("conflict", False),
                quantum_vulnerable=risk["quantum_vulnerable"],
                priority_score=risk["priority_score"],
                priority_label=risk["priority_label"],
                pqc_candidate=risk["pqc_candidate"],
                business_criticality=f.get("business_criticality", "medium"),
                usage=f.get("usage", "unknown"),
                library=f.get("library", ""),
                protocol=f.get("protocol", ""),
                key_size=f.get("key_size"),
                data_sensitivity=f.get("data_sensitivity", "medium"),
                data_lifetime_years=f.get("data_lifetime_years", 10),
                migration_time_years=f.get("migration_time_years", 3),
                threat_horizon_years=f.get("threat_horizon_years", 15),
                exposure=f.get("exposure", "internal"),
                migration_effort=f.get("migration_effort", "medium"),
                risk_reasons=risk["risk_reasons"],
                hybrid_recommended=risk["hybrid_recommended"],
                logical_asset_id=f.get("logical_asset_id", ""),
                evidence_kind=f.get("evidence_kind", "unknown"),
                parser_version=parser_version,
                evidence_quality=str(risk.get("confidence_band", "UNCERTAIN")).lower(),
                confirmed_use=risk.get("confirmed_use", False),
                capability_only=risk.get("capability_only", False),
                risk_context_provenance=risk.get("risk_context_provenance", {}),
                span=f.get("evidence_list", [{}])[0].get("span") or {} if f.get("evidence_list") else {},
                confidence_reasons=confidence_reasons,
            )
            db.add(asset)
            persisted += 1

        avg_conf = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
        job = db.query(ScanJobDB).filter(ScanJobDB.id == scan_id).first()
        job.finished_at = datetime.now(timezone.utc)
        job.status = "completed"
        job.assets_found = persisted
        job.avg_confidence = avg_conf
        job.total_files = metrics["total_files"]
        job.in_scope_files = metrics["in_scope_files"]
        job.scanned_files = metrics["scanned_files"]
        job.failed_files = metrics["failed_files"]
        job.coverage_pct = metrics["coverage_pct"]
        job.duration_ms = metrics["duration_ms"]
        job.collector_stats = metrics["collector_stats"]
        job.blind_spots = list(metrics["blind_spots"])
        for failure in metrics.get("failures", []):
            db.add(ScanFailureDB(
                scan_job_id=scan_id,
                path=failure["path"],
                reason=failure["reason"],
            ))
        db.commit()
        logger.info("Scan complete", extra={"extra_data": {"scan_id": scan_id, "persisted": persisted, "avg_confidence": avg_conf}})
    finally:
        db.close()

    return {
        "scan_id": scan_id,
        "status": "completed",
        "assets_found": persisted,
        "avg_confidence": avg_conf if confidences else 0.0,
        "coverage_pct": metrics["coverage_pct"],
        "collector_stats": metrics["collector_stats"],
    }
