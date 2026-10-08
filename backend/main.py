"""
FastAPI application entrypoint.

Mounts CORS, initialises DB tables on startup, and exposes three routers:
    /api/scan, /api/assets, /api/dashboard
"""

import asyncio
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (ECDAT-ImpactX/.env) before any module reads os.environ.
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env", override=False)

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

import backend.models.audit_log as _audit_log  # registers the audit table  # noqa: F401
import backend.models.scan_dispatch as _scan_dispatch  # registers dispatch outbox  # noqa: F401
import backend.models.scan_lease as _scan_lease  # registers the scan lease table  # noqa: F401
from backend.db import Base, engine
from backend.logging_config import RequestIdFilter, SlowRequestMiddleware, get_logger
from backend.readiness import check_readiness, missing_columns
from backend.routers.assets import router as assets_router
from backend.routers.audit import router as audit_router
from backend.routers.auth import router as auth_router
from backend.routers.dashboard import router as dashboard_router
from backend.routers.observability import router as observability_router
from backend.routers.outputs import router as outputs_router
from backend.routers.scan import router as scan_router
from backend.security import current_role
from backend.settings import SettingsError, get_settings

logger = get_logger("ecdat.app")

SCHEMA_REVISION = "0008_scan_admission"
REQUIRED_TABLES = {
    "scan_jobs",
    "crypto_assets",
    "scan_failures",
    "audit_logs",
    "scan_leases",
    "revoked_sessions",
    "scan_dispatches",
}
# Per-table required column sets (used by /ready column verification).
REQUIRED_COLUMNS = {
    "scan_jobs": {
        "id",
        "repo_path",
        "started_at",
        "finished_at",
        "status",
        "assets_found",
        "avg_confidence",
        "total_files",
        "in_scope_files",
        "scanned_files",
        "failed_files",
        "coverage_pct",
        "duration_ms",
        "collector_stats",
        "blind_spots",
        "result_version",
    },
    "crypto_assets": {
        "id",
        "scan_job_id",
        "algorithm",
        "category",
        "source",
        "location",
        "evidence_json",
        "confidence",
        "conflict",
        "quantum_vulnerable",
        "priority_score",
        "priority_label",
        "pqc_candidate",
        "business_criticality",
        "usage",
        "library",
        "protocol",
        "key_size",
        "data_sensitivity",
        "data_lifetime_years",
        "migration_time_years",
        "threat_horizon_years",
        "exposure",
        "migration_effort",
        "risk_reasons",
        "hybrid_recommended",
        "logical_asset_id",
        "evidence_kind",
        "parser_version",
        "evidence_quality",
        "confirmed_use",
        "capability_only",
        "span",
        "confidence_reasons",
        "created_at",
        "risk_context_provenance",
    },
    "scan_failures": {"id", "scan_job_id", "path", "reason", "created_at"},
    "audit_logs": {
        "id",
        "timestamp",
        "actor_subject",
        "actor_role",
        "actor_session_id",
        "actor_expires_at",
        "action",
        "resource",
        "details",
    },
    "scan_leases": {
        "id",
        "scan_job_id",
        "worker_id",
        "acquired_at",
        "expires_at",
        "released",
    },
    "revoked_sessions": {"session_id", "subject", "expires_at", "revoked_at"},
    "scan_dispatches": {
        "id",
        "scan_job_id",
        "version",
        "state",
        "available_at",
        "claimed_by",
        "claim_expires_at",
        "heartbeat_at",
        "cancellation_requested_at",
        "attempt_count",
        "created_at",
        "updated_at",
    },
}
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Generate/propagate X-Request-ID and inject into thread-local logger."""

    async def dispatch(self, request: Request, call_next):
        supplied_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_id
            if _REQUEST_ID_PATTERN.fullmatch(supplied_id)
            else str(uuid.uuid4())
        )
        RequestIdFilter.set_request_id(request_id)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled exception during request processing")
            raise
        else:
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            RequestIdFilter.clear_request_id()


class TimeoutMiddleware(BaseHTTPMiddleware):
    """Enforce a hard per-request timeout using ``asyncio.wait_for`` on the ASGI call.

    The timeout value is read from the ``ECDAT_REQUEST_TIMEOUT`` environment
    variable (seconds, default 120).  Requests that exceed the limit are
    cancelled with a 504 response.
    """

    def __init__(self, app, timeout: float) -> None:
        super().__init__(app)
        self._timeout = timeout

    async def dispatch(self, request: Request, call_next):
        try:
            response = await asyncio.wait_for(call_next(request), timeout=self._timeout)
        except asyncio.TimeoutError:
            logger.error(
                "Request timeout",
                extra={
                    "extra_data": {
                        "path": str(request.url.path),
                        "timeout_s": self._timeout,
                    }
                },
            )
            raise HTTPException(
                status_code=504,
                detail=f"Request exceeded {self._timeout:.0f}s timeout",
            )
        return response


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # ── Startup migration check ─────────────────────────────────────────────
    # Always validate the Alembic revision regardless of auto-creation setting.
    _auto_create = os.getenv("ECDAT_AUTO_CREATE_TABLES", "false").lower() == "true"
    _db_revision = None
    try:
        from backend.db import SessionLocal

        with SessionLocal() as db:
            _db_revision = db.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
    except Exception:
        pass  # table may not exist yet (fresh DB or unversioned schema)

    if _db_revision is None:
        if _auto_create:
            Base.metadata.create_all(bind=engine)
            # Stamp the freshly-created schema with the expected revision.
            try:
                from pathlib import Path as _P

                from alembic.config import Config as _Cfg

                from alembic import command as _cmd

                _cfg = _Cfg(str(_P(__file__).resolve().parent.parent / "alembic.ini"))
                _cfg.config_file_name = None
                _cfg.set_main_option(
                    "script_location",
                    str(_P(__file__).resolve().parent.parent / "alembic"),
                )
                _cmd.stamp(_cfg, SCHEMA_REVISION)
                logger.info(
                    "DB tables created via create_all and stamped",
                    extra={"extra_data": {"revision": SCHEMA_REVISION}},
                )
            except Exception:
                logger.exception("Failed to stamp Alembic revision after create_all")
        else:
            logger.warning(
                "Database has no alembic_version table — schema may be unversioned",
                extra={"extra_data": {"auto_create": False}},
            )
    else:
        if _db_revision == SCHEMA_REVISION:
            logger.info(
                "Schema revision verified",
                extra={"extra_data": {"revision": _db_revision}},
            )
        else:
            logger.warning(
                "Schema revision mismatch: expected %s, found %s",
                SCHEMA_REVISION,
                _db_revision,
                extra={
                    "extra_data": {"expected": SCHEMA_REVISION, "found": _db_revision}
                },
            )

    # ── Optional create_all (dev convenience, now opt-in) ───────────────────
    if _auto_create and _db_revision is not None:
        Base.metadata.create_all(bind=engine)
        logger.info("DB tables ensured via create_all (revision already present)")

    # ── Stale-scan recovery on startup (dry-run by default). ─────────────────
    _recover = os.getenv("ECDAT_RECOVER_STALE_JOBS", "false").lower() == "true"
    if _recover:
        try:
            from backend.services.stale_job_recovery import recover_stale_jobs

            count = recover_stale_jobs(dry_run=False)
            logger.info(
                "Stale-job recovery complete",
                extra={"extra_data": {"recovered": count}},
            )
        except Exception:
            logger.exception("Stale-job recovery failed")
    else:
        logger.info(
            "Stale-job recovery skipped (dry-run mode — set ECDAT_RECOVER_STALE_JOBS=true to apply)"
        )

    # ── Reconcile expired DB-backed scan leases on startup. ─────────────────
    try:
        from backend.db import SessionLocal
        from backend.services.scan_lease import reconcile_expired_leases

        with SessionLocal() as db:
            count = reconcile_expired_leases(db)
            db.commit()
        logger.info(
            "Startup lease reconciliation complete",
            extra={"extra_data": {"reconciled": count}},
        )
    except Exception:
        logger.exception("Startup lease reconciliation failed")

    async def scan_watchdog():
        from backend.services.scan_control import reconcile_abandoned_scans

        while True:
            try:
                await asyncio.to_thread(reconcile_abandoned_scans)
            except Exception:
                logger.exception("Scan watchdog reconciliation failed")
            await asyncio.sleep(10)

    watchdog = asyncio.create_task(scan_watchdog())
    try:
        yield
    finally:
        watchdog.cancel()
        try:
            await watchdog
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="ECDAT",
    description="Enterprise Cryptographic Discovery & Analysis Tool — ImpactX 2026",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request, exc: RequestValidationError):
    # Never reflect passwords/raw bodies or invalid Unicode into error responses.
    details = [
        {"loc": error["loc"], "type": error["type"], "msg": "Invalid request value"}
        for error in exc.errors()
    ]
    return Response(
        json.dumps({"detail": details}, ensure_ascii=True),
        status_code=422,
        media_type="application/json",
    )


# ── Middleware ────────────────────────────────────────────────────────────────
app.add_middleware(RequestIdMiddleware)
app.add_middleware(SlowRequestMiddleware)

if os.getenv("ECDAT_ENABLE_PROMETHEUS", "false").lower() == "true":

    class PrometheusMiddleware(BaseHTTPMiddleware):
        """Record request count and duration for Prometheus exposition."""

        async def dispatch(self, request: Request, call_next):
            from backend.services.prometheus_metrics import (
                inc_request,
                observe_request_duration,
            )

            started = time.perf_counter()
            try:
                response = await call_next(request)
                inc_request(request.method, request.url.path, response.status_code)
                observe_request_duration(
                    request.method,
                    request.url.path,
                    response.status_code,
                    time.perf_counter() - started,
                )
                return response
            except Exception:
                inc_request(request.method, request.url.path, 500)
                observe_request_duration(
                    request.method, request.url.path, 500, time.perf_counter() - started
                )
                raise

    app.add_middleware(PrometheusMiddleware)

# Build middleware from the same typed settings used by readiness and auth.  An
# invalid deployment still boots far enough for /ready to return a stable 503.
try:
    _application_settings = get_settings()
    _request_timeout = _application_settings.request_timeout_seconds
    _cors_origins = list(_application_settings.cors_origins)
except SettingsError as exc:
    logger.error("Startup configuration validation failed: %s", exc)
    _request_timeout = 120.0
    _cors_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]

app.add_middleware(TimeoutMiddleware, timeout=_request_timeout)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count", "X-Request-ID"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(scan_router, dependencies=[Depends(current_role)])
app.include_router(assets_router, dependencies=[Depends(current_role)])
app.include_router(dashboard_router, dependencies=[Depends(current_role)])
app.include_router(outputs_router, dependencies=[Depends(current_role)])
app.include_router(audit_router)
app.include_router(auth_router)
app.include_router(observability_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ecdat-backend", "version": app.version}


@app.get("/ready")
def readiness() -> dict:
    """Confirm that both the API process and database are ready for a demo scan."""
    return check_readiness(
        schema_revision=SCHEMA_REVISION,
        required_tables=REQUIRED_TABLES,
        required_columns=REQUIRED_COLUMNS,
        logger=logger,
    )


def _check_required_columns(insp) -> set[str]:
    """Return set of missing column names across all required tables."""
    return missing_columns(insp, REQUIRED_COLUMNS)
