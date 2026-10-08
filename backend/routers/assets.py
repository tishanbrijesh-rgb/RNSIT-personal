"""Assets router — list and retrieve crypto assets."""
import csv
import io
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import or_

from backend.db import SessionLocal
from backend.logging_config import get_logger
from backend.models.asset import CryptoAssetDB
from backend.models.scan_job import ScanJobDB
from backend.schemas.asset import AssetResponse, AssetUpdate
from backend.security import current_role, ensure_write_role, record_audit

logger = get_logger("ecdat.assets")
router = APIRouter(prefix="/api", tags=["assets"])

CSV_COLUMNS = (
    "id", "algorithm", "key_size", "category", "priority_label",
    "priority_score", "confidence", "quantum_vulnerable", "location",
    "usage", "library", "protocol", "evidence_kind", "pqc_candidate",
)


def _asset_query(db, scan_job_id, search, risk, quantum):
    target_scan_id = scan_job_id
    if target_scan_id is None:
        latest = (db.query(ScanJobDB).filter(ScanJobDB.status == "completed")
                  .order_by(ScanJobDB.id.desc()).first())
        target_scan_id = latest.id if latest else None
    q = db.query(CryptoAssetDB).filter(CryptoAssetDB.scan_job_id == target_scan_id)
    if search_text := (search.strip() if search else ""):
        escaped = search_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        q = q.filter(or_(
            CryptoAssetDB.algorithm.ilike(pattern, escape="\\"),
            CryptoAssetDB.category.ilike(pattern, escape="\\"),
            CryptoAssetDB.location.ilike(pattern, escape="\\"),
            CryptoAssetDB.library.ilike(pattern, escape="\\"),
            CryptoAssetDB.protocol.ilike(pattern, escape="\\"),
            CryptoAssetDB.usage.ilike(pattern, escape="\\"),
        ))
    if risk is not None:
        q = q.filter(CryptoAssetDB.priority_label == risk)
    if quantum is not None:
        q = q.filter(CryptoAssetDB.quantum_vulnerable.is_(quantum))
    return q


def _sort_assets(q, sort):
    if sort == "confidence":
        return q.order_by(CryptoAssetDB.confidence.desc(), CryptoAssetDB.id.desc())
    if sort == "algorithm":
        return q.order_by(CryptoAssetDB.algorithm.asc(), CryptoAssetDB.id.desc())
    return q.order_by(CryptoAssetDB.priority_score.desc(), CryptoAssetDB.id.desc())


@router.get("/assets.csv")
def export_assets(
    scan_job_id: int | None = Query(default=None, ge=1),
    search: str | None = Query(default=None, alias="q", min_length=1, max_length=200, pattern=r".*\S.*"),
    risk: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] | None = Query(default=None),
    quantum: bool | None = Query(default=None),
    sort: Literal["priority", "confidence", "algorithm"] = Query(default="priority"),
    ids: Annotated[list[int] | None, Query()] = None,
) -> StreamingResponse:
    if ids is not None and not ids:
        raise HTTPException(422, detail="Select at least one asset")

    def rows():
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        yield output.getvalue()
        db = SessionLocal()
        try:
            q = _asset_query(db, scan_job_id, None if ids is not None else search,
                             None if ids is not None else risk,
                             None if ids is not None else quantum)
            if ids is not None:
                q = q.filter(CryptoAssetDB.id.in_(ids))
            for asset in _sort_assets(q, sort).yield_per(250):
                output.seek(0)
                output.truncate(0)
                writer.writerow([getattr(asset, column) for column in CSV_COLUMNS])
                yield output.getvalue()
        finally:
            db.close()

    return StreamingResponse(
        rows(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="ecdat-assets.csv"'},
    )


@router.get("/assets", response_model=list[AssetResponse])
def list_assets(
    scan_job_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(
        default=None, alias="q", min_length=1, max_length=200, pattern=r".*\S.*"
    ),
    risk: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] | None = Query(default=None),
    quantum: bool | None = Query(default=None),
    sort: Literal["priority", "confidence", "algorithm"] = Query(default="priority"),
) -> JSONResponse:
    """List assets, with optional server-side filtering and pagination.

    ``X-Total-Count`` header describes the filtered result set before
    pagination. Body contains ``items`` and ``total`` fields.
    """
    db = SessionLocal()
    try:
        q = _asset_query(db, scan_job_id, search, risk, quantum)
        total = q.count()
        if offset >= total:
            return JSONResponse(content={"items": [], "total": total}, headers={"X-Total-Count": str(total)})
        q = _sort_assets(q, sort).offset(offset).limit(limit)
        assets = q.all()
        validated = [AssetResponse.model_validate(a) for a in assets]
        items = [a.model_dump(mode="json") for a in validated]
        return JSONResponse(
            content={"items": items, "total": total},
            headers={"X-Total-Count": str(total)},
        )
    finally:
        db.close()


@router.get("/assets/{asset_id}", response_model=AssetResponse)
def get_asset(asset_id: int) -> AssetResponse:
    """Get a single asset with full evidence detail."""
    db = SessionLocal()
    try:
        asset = db.query(CryptoAssetDB).filter(CryptoAssetDB.id == asset_id).first()
        if not asset:
            logger.info("Asset not found", extra={"extra_data": {"asset_id": asset_id}})
            raise HTTPException(404, detail="Asset not found")
        logger.info("Asset retrieved", extra={"extra_data": {"asset_id": asset_id, "scan_job_id": asset.scan_job_id}})
        return AssetResponse.model_validate(asset)
    finally:
        db.close()


@router.patch("/assets/{asset_id}", response_model=AssetResponse)
def update_asset(asset_id: int, payload: AssetUpdate, role: str = Depends(current_role)) -> AssetResponse:
    """Update asset fields — e.g. business_criticality."""
    from backend.services.risk_engine import assess_risk

    db = SessionLocal()
    try:
        ensure_write_role(role)
        asset = db.query(CryptoAssetDB).filter(CryptoAssetDB.id == asset_id).first()
        if not asset:
            raise HTTPException(404, detail="Asset not found")

        changes = payload.model_dump(exclude_none=True)
        for field, value in changes.items():
            setattr(asset, field, value)
        risk = assess_risk({
            "algorithm": asset.algorithm, "usage": asset.usage,
            "business_criticality": asset.business_criticality,
            "data_sensitivity": asset.data_sensitivity,
            "data_lifetime_years": asset.data_lifetime_years,
            "migration_time_years": asset.migration_time_years,
            "threat_horizon_years": asset.threat_horizon_years,
            "exposure": asset.exposure, "migration_effort": asset.migration_effort,
        })
        asset.priority_score = risk["priority_score"]
        asset.priority_label = risk["priority_label"]
        asset.pqc_candidate = risk["pqc_candidate"]
        asset.quantum_vulnerable = risk["quantum_vulnerable"]
        asset.risk_reasons = risk["risk_reasons"]
        asset.hybrid_recommended = risk["hybrid_recommended"]
        asset.risk_context_provenance = risk.get("risk_context_provenance", {})

        record_audit(
            "asset.risk_context_updated",
            f"asset:{asset_id}",
            role,
            changes,
            session=db,
        )
        db.commit()
        db.refresh(asset)
        return AssetResponse.model_validate(asset)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
