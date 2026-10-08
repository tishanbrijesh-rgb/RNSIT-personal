"""Scan router — start scans, query scan job history, and stream live progress."""
import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StrictStr

from backend.db import SessionLocal
from backend.logging_config import get_logger
from backend.middleware.rate_limit import limit as rate_limit
from backend.models.scan_job import ScanJobDB
from backend.schemas.asset import ScanJobResponse
from backend.security import current_role, ensure_write_role, record_audit
from backend.services.repository_guard import resolve_repository
from backend.services.scan_control import (
    _finish_if_active,  # noqa: F401 - compatibility patch point for existing tests
    claim,  # noqa: F401 - compatibility patch point for existing tests
    dispatch_scan,
    enqueue_scan,
    release,  # noqa: F401 - compatibility patch point for existing tests
    request_cancel,
    reserve,  # noqa: F401 - compatibility patch point for existing tests
    supervise,  # noqa: F401 - compatibility patch point for existing tests
)

router = APIRouter(prefix="/api", tags=["scan"])
logger = get_logger("ecdat.scan")


async def _event_stream(request: Request, scan_id: int, max_events: int = 30) -> AsyncGenerator[str, None]:
    """Poll the scan job DB record and yield SSE events until terminal, disconnect, or max_events.

    The max_events bound prevents indefinite streaming in tests and against
    misbehaving clients.
    """
    terminal = {"completed", "failed", "cancelled", "timed_out"}
    last_payload = ""
    event_count = 0
    try:
        while True:
            if await request.is_disconnected():
                break
            if event_count >= max_events:
                break
            with SessionLocal() as db:
                job = db.get(ScanJobDB, scan_id)
            if job is None:
                yield f"event: error\ndata: {json.dumps({'detail': 'Scan job not found'})}\n\n"
                break
            status = job.status or "unknown"
            payload = json.dumps({
                "scan_id": job.id,
                "status": status,
                "collector_stats": job.collector_stats or {},
                "assets_found": job.assets_found,
                "findings_count": (job.collector_stats or {}).get("_findings_count", job.assets_found),
                "total_files": job.total_files,
                "in_scope_files": job.in_scope_files,
                "scanned_files": job.scanned_files,
                "coverage_pct": job.coverage_pct,
                "duration_ms": job.duration_ms,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "blind_spots": list(job.blind_spots or []),
            })
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            event_count += 1
            if status in terminal:
                yield f"event: done\ndata: {payload}\n\n"
                break
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass


@router.get("/scans/{scan_id}/events")
async def scan_events(
    scan_id: int,
    request: Request,
    max_events: int = Query(default=300, ge=1, le=10000),
) -> StreamingResponse:
    """Stream SSE progress events for an in-progress or completed scan.

    The stream terminates when the scan reaches a terminal state, the client
    disconnects, or max_events polls elapse — whichever comes first.
    """
    logger.info("SSE events requested", extra={"extra_data": {"scan_id": scan_id, "max_events": max_events}})
    with SessionLocal() as db:
        job = db.get(ScanJobDB, scan_id)
        if job is None:
            logger.warning("SSE events: scan job not found", extra={"extra_data": {"scan_id": scan_id}})
            raise HTTPException(404, "Scan job not found")
    logger.info("SSE stream opened", extra={"extra_data": {"scan_id": scan_id, "status": job.status}})
    return StreamingResponse(
        _event_stream(request, scan_id, max_events=max_events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


class ScanRequest(BaseModel):
    repo_path: StrictStr = Field(min_length=1, max_length=4096)


@router.post("/scan", response_model=dict)
@rate_limit(threshold=3, window=300)
def post_scan(
    request: Request,
    payload: ScanRequest,
    background_tasks: BackgroundTasks,
    role: str = Depends(current_role),
) -> dict:
    """Start a new scan. Body: {"repo_path": "..."}."""
    logger.info("Scan POST", extra={"extra_data": {"role": role, "repo": payload.repo_path}})
    repo_path = payload.repo_path
    if not repo_path:
        raise HTTPException(400, detail="repo_path is required")
    ensure_write_role(role)
    repo_path = resolve_repository(repo_path)
    db = SessionLocal()
    try:
        scan_id = enqueue_scan(db, repo_path)
    finally:
        db.close()
    try:
        record_audit("scan.started", f"scan:{scan_id}", role, {"repo_path": repo_path})
        background_tasks.add_task(dispatch_scan, scan_id)
    except Exception:
        logger.exception("Unable to notify dispatcher; queued scan remains durable")
        raise
    logger.info("Scan queued", extra={"extra_data": {"scan_id": scan_id, "role": role, "repo": repo_path}})
    # Preserve the public acceptance response while the durable job remains
    # explicitly queued until a dispatcher owns it.
    return {"scan_id": scan_id, "status": "started"}


@router.post('/scans/{scan_id}/cancel', status_code=202)
def cancel_scan(scan_id: int, role: str = Depends(current_role)) -> dict:
    ensure_write_role(role)
    with SessionLocal() as db:
        job = db.get(ScanJobDB, scan_id)
        if job is None:
            logger.warning("Cancel requested but scan not found", extra={"extra_data": {"scan_id": scan_id}})
            raise HTTPException(404, 'Scan job not found')
        if job.status not in {'pending', 'queued', 'running'}:
            raise HTTPException(409, 'Scan is already finished')
    request_cancel(scan_id)
    record_audit('scan.cancel_requested', f'scan:{scan_id}', role, {})
    logger.info("Cancel requested", extra={"extra_data": {"scan_id": scan_id, "role": role}})
    return {'scan_id': scan_id, 'status': 'cancellation_requested'}


@router.get("/scans", response_model=list[ScanJobResponse])
def list_scans(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    """List all scan jobs, most recent first, with pagination."""
    db = SessionLocal()
    try:
        total = db.query(ScanJobDB).count()
        jobs = (
            db.query(ScanJobDB)
            .order_by(ScanJobDB.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        validated = [ScanJobResponse.model_validate(j) for j in jobs]
        logger.debug("Listed scans", extra={"extra_data": {"count": len(validated), "total": total, "offset": offset}})
        return JSONResponse(
            content=[j.model_dump(mode="json") for j in validated],
            headers={"X-Total-Count": str(total)},
        )
    finally:
        db.close()


@router.get("/scans/{scan_id}", response_model=ScanJobResponse)
def get_scan(scan_id: int) -> ScanJobResponse:
    """Get a single scan job detail."""
    db = SessionLocal()
    try:
        job = db.query(ScanJobDB).filter(ScanJobDB.id == scan_id).first()
        if not job:
            logger.debug("Scan not found", extra={"extra_data": {"scan_id": scan_id}})
            raise HTTPException(404, detail="Scan job not found")
        logger.debug("Scan retrieved", extra={"extra_data": {"scan_id": scan_id, "status": job.status}})
        return ScanJobResponse.model_validate(job)
    finally:
        db.close()
