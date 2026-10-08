"""CBOM, risk-report, evidence-graph, and evaluation output endpoints."""
from __future__ import annotations

import csv
import io
import json
from collections import Counter
from typing import Annotated
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func

from backend.db import SessionLocal
from backend.logging_config import get_logger
from backend.models.asset import CryptoAssetDB
from backend.models.scan_job import ScanJobDB
from backend.services.calibration import classify_confidence, load_calibration_params
from backend.services.evaluation import evaluate_assets

logger = get_logger("ecdat.outputs")
router = APIRouter(prefix="/api", tags=["outputs"])
ScanId = Annotated[int | None, Query(ge=1)]
PageLimit = Annotated[int, Query(ge=1, le=250)]
PageOffset = Annotated[int, Query(ge=0)]
RiskFilter = Annotated[str | None, Query(pattern="^(CRITICAL|HIGH|MEDIUM|LOW)$")]
SearchQuery = Annotated[str | None, Query(max_length=200)]

# Interactive graph responses are intentionally bounded. Complete inventory data
# remains available through the paginated CBOM/risk endpoints and CSV exports.
GRAPH_ASSET_LIMIT = 200
GRAPH_NODE_LIMIT = 500
GRAPH_EDGE_LIMIT = 500
EVALUATION_ASSET_LIMIT = 10_000

def _scan_and_assets(db, scan_id: int | None):
    scan = _resolve_scan(db, scan_id)
    assets = (
        db.query(CryptoAssetDB)
        .filter(CryptoAssetDB.scan_job_id == scan.id)
        .order_by(CryptoAssetDB.id.asc())
        .limit(EVALUATION_ASSET_LIMIT + 1)
        .all()
    )
    if len(assets) > EVALUATION_ASSET_LIMIT:
        raise HTTPException(
            413,
            f"Evaluation is limited to {EVALUATION_ASSET_LIMIT:,} assets",
        )
    return scan, assets


def _resolve_scan(db, scan_id: int | None) -> ScanJobDB:
    if scan_id is None:
        scan = (
            db.query(ScanJobDB)
            .filter(ScanJobDB.status == "completed")
            .order_by(ScanJobDB.id.desc())
            .first()
        )
    else:
        scan = db.query(ScanJobDB).filter(ScanJobDB.id == scan_id).first()
        if scan is not None and scan.status != "completed":
            raise HTTPException(409, "Scan is not complete")
    if scan is None:
        raise HTTPException(404, "No completed scan found")
    return scan

@router.get("/cbom")
def cbom(
    scan_id: ScanId = None,
    limit: PageLimit = 100,
    offset: PageOffset = 0,
    q: SearchQuery = None,
) -> dict:
    db = SessionLocal()
    try:
        scan = _resolve_scan(db, scan_id)
        base_query = db.query(CryptoAssetDB).filter(CryptoAssetDB.scan_job_id == scan.id)
        total = base_query.count()
        filtered_query = base_query
        if q and q.strip():
            needle = f"%{q.strip()}%"
            filtered_query = filtered_query.filter(
                CryptoAssetDB.algorithm.ilike(needle)
                | CryptoAssetDB.location.ilike(needle)
                | CryptoAssetDB.category.ilike(needle)
                | CryptoAssetDB.usage.ilike(needle)
            )
        filtered_total = filtered_query.count()
        assets = (
            filtered_query.order_by(
                CryptoAssetDB.algorithm.asc(),
                CryptoAssetDB.location.asc(),
                CryptoAssetDB.id.asc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        logger.info("CBOM generated", extra={"extra_data": {"scan_id": scan.id, "assets": len(assets)}})
        def properties(asset: CryptoAssetDB) -> list[dict[str, str]]:
            evidence = asset.evidence_json or {}
            values = {
                "ecdat:asset:id": asset.logical_asset_id,
                "ecdat:category": asset.category,
                "ecdat:usage": asset.usage,
                "ecdat:evidence-kind": asset.evidence_kind,
                "ecdat:parser-version": asset.parser_version,
                "ecdat:evidence-quality": asset.evidence_quality,
                "ecdat:confirmed-use": asset.confirmed_use,
                "ecdat:capability-only": asset.capability_only,
                "ecdat:operation-anchor": evidence.get("operation_anchor", ""),
                "ecdat:context-conflicts": evidence.get("context_conflicts", {}),
                "ecdat:location": asset.location,
                "ecdat:library": asset.library,
                "ecdat:protocol": asset.protocol,
                "ecdat:key-size": asset.key_size,
                "ecdat:evidence:sources": asset.source,
                "ecdat:confidence": asset.confidence,
                "ecdat:quantum-vulnerable": asset.quantum_vulnerable,
                "ecdat:risk-score": asset.priority_score,
                "ecdat:risk-label": asset.priority_label,
                "ecdat:migration-recommendation": asset.pqc_candidate,
            }
            return [
                {"name": name, "value": (
                    str(value).lower() if isinstance(value, bool) else
                    json.dumps(value, sort_keys=True) if isinstance(value, (list, dict)) else
                    str(value)
                )}
                for name, value in values.items()
                if value not in (None, "", [], {})
            ]

        return {
            "$schema": "https://cyclonedx.org/schema/bom-1.6.schema.json",
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "serialNumber": f"urn:uuid:{uuid5(NAMESPACE_URL, f'ecdat:scan:{scan.id}')}",
            "version": 1,
            "metadata": {
                "tools": {"components": [{
                    "type": "application", "name": "ECDAT", "version": "1.0.0"
                }]},
                "properties": [
                    {"name": "ecdat:scan:id", "value": str(scan.id)},
                    {"name": "ecdat:repository:path", "value": scan.repo_path},
                    {"name": "ecdat:scan:coverage-percent", "value": str(scan.coverage_pct)},
                ],
            },
            "pagination": {"total": total, "filtered": filtered_total, "offset": offset, "limit": limit, "loaded": len(assets)},
            "components": [{
                "type": "library",
                "bom-ref": f"ecdat:asset:{asset.id}",
                "name": asset.algorithm,
                "properties": properties(asset),
            } for asset in assets],
        }
    finally: db.close()


def _csv_row(values: list[object]) -> str:
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerow(values)
    return output.getvalue()


@router.get("/cbom.csv")
def cbom_csv(scan_id: ScanId = None) -> StreamingResponse:
    db = SessionLocal()
    try:
        scan = _resolve_scan(db, scan_id)
        resolved_scan_id = int(scan.id)
        exported_count = (
            db.query(CryptoAssetDB)
            .filter(CryptoAssetDB.scan_job_id == resolved_scan_id)
            .count()
        )
    finally:
        db.close()

    def rows():
        yield _csv_row(["name", "type", "location", "category", "confidence"])
        export_db = SessionLocal()
        try:
            assets = (
                export_db.query(CryptoAssetDB)
                .filter(CryptoAssetDB.scan_job_id == resolved_scan_id)
                .order_by(
                    CryptoAssetDB.algorithm.asc(),
                    CryptoAssetDB.location.asc(),
                    CryptoAssetDB.id.asc(),
                )
                .yield_per(250)
            )
            for asset in assets:
                yield _csv_row(
                    [asset.algorithm, "library", asset.location, asset.category, asset.confidence]
                )
        finally:
            export_db.close()

    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=ecdat-cbom.csv",
            "X-Exported-Count": str(exported_count),
        },
    )

@router.get("/reports/risk")
def risk_report(
    scan_id: ScanId = None,
    limit: PageLimit = 100,
    offset: PageOffset = 0,
    risk: RiskFilter = None,
    q: SearchQuery = None,
) -> dict:
    db = SessionLocal()
    try:
        scan = _resolve_scan(db, scan_id)
        base_query = db.query(CryptoAssetDB).filter(CryptoAssetDB.scan_job_id == scan.id)
        total = base_query.count()
        # Count each risk label without materialising complete asset rows.
        distribution = Counter()
        for label, count in (
            base_query
            .group_by(CryptoAssetDB.priority_label)
            .with_entities(CryptoAssetDB.priority_label, func.count(CryptoAssetDB.id))
            .all()
        ):
            distribution[label] = count
        quantum_vulnerable = base_query.filter(CryptoAssetDB.quantum_vulnerable.is_(True)).count()
        conflicts = base_query.filter(CryptoAssetDB.conflict.is_(True)).count()
        filtered_query = base_query
        if risk:
            filtered_query = filtered_query.filter(CryptoAssetDB.priority_label == risk)
        if q and q.strip():
            needle = f"%{q.strip()}%"
            filtered_query = filtered_query.filter(
                CryptoAssetDB.algorithm.ilike(needle)
                | CryptoAssetDB.location.ilike(needle)
                | CryptoAssetDB.category.ilike(needle)
                | CryptoAssetDB.usage.ilike(needle)
            )
        filtered_total = filtered_query.count()
        ranked = (
            filtered_query.order_by(
                CryptoAssetDB.priority_score.desc(),
                CryptoAssetDB.algorithm.asc(),
                CryptoAssetDB.location.asc(),
                CryptoAssetDB.id.asc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        logger.info("Risk report generated", extra={"extra_data": {"scan_id": scan.id}})
        params = load_calibration_params()
        return {
            "title": "ECDAT Cryptographic Risk and PQC Migration Report", "scan_id": scan.id,
            "repository": scan.repo_path, "coverage_pct": scan.coverage_pct,
            "summary": {"assets": total, "quantum_vulnerable": quantum_vulnerable, "conflicts": conflicts, "risk_distribution": dict(distribution)},
            "pagination": {"total": total, "filtered": filtered_total, "offset": offset, "limit": limit, "loaded": len(ranked)},
            "blind_spots": list(scan.blind_spots or []),
            "calibration": {"version": params.version, "brier_score": params.brier_score, "ece": params.ece},
            "migration_priorities": [{
                "asset_id": a.id, "logical_asset_id": a.logical_asset_id,
                "algorithm": a.algorithm, "usage": a.usage,
                "operation_anchor": (a.evidence_json or {}).get("operation_anchor", ""),
                "location": a.location, "score": a.priority_score, "label": a.priority_label,
                "confidence": a.confidence,
                "confidence_band": classify_confidence(a.confidence)["band"],
                "confidence_interpretation": classify_confidence(a.confidence)["description"],
                "reasons": a.risk_reasons, "recommendation": a.pqc_candidate,
                "hybrid": a.hybrid_recommended} for a in ranked],
        }
    finally: db.close()


@router.get("/reports/risk.csv")
def risk_report_csv(scan_id: ScanId = None) -> StreamingResponse:
    db = SessionLocal()
    try:
        scan = _resolve_scan(db, scan_id)
        resolved_scan_id = int(scan.id)
        exported_count = (
            db.query(CryptoAssetDB)
            .filter(CryptoAssetDB.scan_job_id == resolved_scan_id)
            .count()
        )
    finally:
        db.close()

    def rows():
        yield _csv_row(
            ["algorithm", "location", "score", "level", "hybrid", "recommendation", "reasons"]
        )
        export_db = SessionLocal()
        try:
            assets = (
                export_db.query(CryptoAssetDB)
                .filter(CryptoAssetDB.scan_job_id == resolved_scan_id)
                .order_by(
                    CryptoAssetDB.priority_score.desc(),
                    CryptoAssetDB.algorithm.asc(),
                    CryptoAssetDB.location.asc(),
                    CryptoAssetDB.id.asc(),
                )
                .yield_per(250)
            )
            for asset in assets:
                yield _csv_row(
                    [
                        asset.algorithm,
                        asset.location,
                        asset.priority_score,
                        asset.priority_label,
                        "yes" if asset.hybrid_recommended else "no",
                        asset.pqc_candidate,
                        "; ".join(asset.risk_reasons or []),
                    ]
                )
        finally:
            export_db.close()

    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=ecdat-risk-report.csv",
            "X-Exported-Count": str(exported_count),
        },
    )


@router.get("/calibration")
def calibration_info() -> dict:
    """Return current calibration parameters and sample classifications."""
    from backend.services.calibration import BAND_META, load_calibration_params

    params = load_calibration_params()
    sample_scores = [0.95, 0.72, 0.55, 0.30]
    samples = [{"score": s, **classify_confidence(s)} for s in sample_scores]
    band_definitions = {
        band: {"label": meta["label"], "description": meta["description"]}
        for band, meta in BAND_META.items()
    }
    return {
        "version": params.version,
        "band_definitions": band_definitions,
        "band_thresholds": {k: list(v) for k, v in params.band_thresholds.items()},
        "metrics": {"brier_score": params.brier_score, "ece": params.ece},
        "last_calibrated": params.last_calibrated,
        "sample_classifications": samples,
    }

@router.get("/evidence-graph")
def evidence_graph(scan_id: ScanId = None, asset_id: int | None = Query(default=None, ge=1)) -> dict:
    db = SessionLocal()
    try:
        scan = _resolve_scan(db, scan_id)
        asset_query = (
            db.query(CryptoAssetDB)
            .filter(CryptoAssetDB.scan_job_id == scan.id)
            .order_by(CryptoAssetDB.id.asc())
        )
        if asset_id is not None:
            asset_query = asset_query.filter(CryptoAssetDB.id == asset_id)
        total_assets = asset_query.count()
        if asset_id is not None and total_assets == 0:
            raise HTTPException(404, detail="Asset not found in scan")
        assets = asset_query.limit(GRAPH_ASSET_LIMIT + 1).all()
        asset_limit_reached = len(assets) > GRAPH_ASSET_LIMIT
        assets = assets[:GRAPH_ASSET_LIMIT]
        logger.info(
            "Evidence graph generated",
            extra={"extra_data": {"scan_id": scan.id, "assets": len(assets)}},
        )
        nodes, edges = [], []
        returned_assets = 0
        truncation_reasons: list[str] = []

        def note_truncation(reason: str) -> None:
            if reason not in truncation_reasons:
                truncation_reasons.append(reason)

        if asset_limit_reached:
            note_truncation("asset_limit")
        for asset in assets:
            if len(nodes) >= GRAPH_NODE_LIMIT:
                note_truncation("node_limit")
                break
            asset_node = f"asset:{asset.id}"
            nodes.append({"id": asset_node, "type": "asset",
                          "label": f"{asset.algorithm} ({asset.usage})",
                          "logical_asset_id": asset.logical_asset_id,
                          "operation_anchor": (asset.evidence_json or {}).get("operation_anchor", ""),
                          "confidence": asset.confidence,
                          "priority_score": asset.priority_score,
                          "priority_label": asset.priority_label,
                          "quantum_vulnerable": asset.quantum_vulnerable})
            returned_assets += 1
            seen_sources: set[str] = set()
            for source in asset.source:
                label = source.upper()
                if label in seen_sources:
                    continue
                seen_sources.add(label)
                blocked = False
                if len(nodes) >= GRAPH_NODE_LIMIT:
                    note_truncation("node_limit")
                    blocked = True
                if len(edges) >= GRAPH_EDGE_LIMIT:
                    note_truncation("edge_limit")
                    blocked = True
                if blocked:
                    continue
                source_node = f"source:{asset.id}:{source}"
                nodes.append({"id": source_node, "type": "evidence", "label": label})
                edges.append({"source": source_node, "target": asset_node, "relation": "supports"})
        return {
            "scan_id": scan.id,
            "nodes": nodes,
            "edges": edges,
            "truncation": {
                "truncated": bool(truncation_reasons),
                "limits": {
                    "assets": GRAPH_ASSET_LIMIT,
                    "nodes": GRAPH_NODE_LIMIT,
                    "edges": GRAPH_EDGE_LIMIT,
                },
                "returned": {
                    "assets": returned_assets,
                    "nodes": len(nodes),
                    "edges": len(edges),
                },
                "total_assets": total_assets,
                "reasons": truncation_reasons,
            },
        }
    finally: db.close()

@router.get("/evaluation")
def evaluation(scan_id: ScanId = None) -> dict:
    db = SessionLocal()
    try:
        scan, assets = _scan_and_assets(db, scan_id)
        result = evaluate_assets(assets, scan.repo_path)
        result.update({"scan_id": scan.id, "coverage_pct": scan.coverage_pct, "duration_ms": scan.duration_ms})
        return result
    finally: db.close()

@router.get("/reports/risk.txt")
def risk_report_text(scan_id: ScanId = None) -> Response:
    # This legacy download remains complete; interactive JSON is paginated.
    report = risk_report(scan_id, limit=2_147_483_647, offset=0, risk=None, q=None)
    lines = [report["title"], f"Repository: {report['repository']}", f"Coverage: {report['coverage_pct']}%", "", "Migration priorities:"]
    lines.extend(f"P{index + 1} | {item['label']} {item['score']}/100 | {item['algorithm']} ({item['usage']}) | {item['location']} | {item['operation_anchor']} | {item['recommendation']}" for index, item in enumerate(report["migration_priorities"]))
    return Response("\n".join(lines), media_type="text/plain", headers={"Content-Disposition": "attachment; filename=ecdat-risk-report.txt"})
