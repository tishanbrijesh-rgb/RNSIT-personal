"""Single-server-process scan admission with DB-backed worker leases."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import and_, or_, update

from backend.db import SessionLocal
from backend.logging_config import get_logger
from backend.models.scan_dispatch import ScanDispatchDB
from backend.models.scan_job import ScanJobDB
from backend.security import record_audit
from backend.services.scan_lease import (
    acquire_lease,
    heartbeat_lease,
    release_lease,
    worker_identity,
)
from scanner.limits import max_file_bytes, positive_int

logger = get_logger("ecdat.scan_control")

SUPERVISOR_GRACE_SECONDS = 45


def _cancel_signal(scan_id: int) -> Path:
    return Path(__file__).resolve().parents[2] / ".runtime" / f"scan-{scan_id}.cancel"


@dataclass
class Control:
    timeout: int
    scan_id: int | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    worker_id: str = field(default_factory=worker_identity)
    version: int = 1
    durable_claim: bool = False


_lock = threading.Lock()
_active: Control | None = None


def enqueue_scan(db_session, repo_path: str) -> int:
    """Atomically persist an accepted scan and its durable dispatch entry."""
    active = (
        db_session.query(ScanJobDB)
        .filter(
            ScanJobDB.repo_path == repo_path,
            ScanJobDB.status.in_(("pending", "queued", "running")),
        )
        .order_by(ScanJobDB.id.desc())
        .first()
    )
    if active is not None:
        return active.id
    job = ScanJobDB(repo_path=repo_path, status="queued")
    db_session.add(job)
    db_session.flush()
    db_session.add(ScanDispatchDB(scan_job_id=job.id, version=1, state="pending"))
    db_session.commit()
    return job.id


def claim_dispatch(
    db_session,
    scan_id: int,
    worker_id: str,
    now: datetime,
    ttl: timedelta,
) -> bool:
    """Atomically claim pending work or reclaim a confirmed-expired claim."""
    eligible = or_(
        and_(
            ScanDispatchDB.state == "pending",
            ScanDispatchDB.available_at <= now,
        ),
        and_(
            ScanDispatchDB.state == "claimed",
            ScanDispatchDB.claim_expires_at < now,
        ),
    )
    result = db_session.execute(
        update(ScanDispatchDB)
        .where(ScanDispatchDB.scan_job_id == scan_id, eligible)
        .values(
            state="claimed",
            claimed_by=worker_id,
            claim_expires_at=now + ttl,
            heartbeat_at=now,
            attempt_count=ScanDispatchDB.attempt_count + 1,
            updated_at=now,
        )
    )
    return result.rowcount == 1


def heartbeat_dispatch(
    db_session,
    scan_id: int,
    worker_id: str,
    now: datetime,
    ttl: timedelta,
) -> bool:
    """Extend a live dispatch claim only for its current owner."""
    result = db_session.execute(
        update(ScanDispatchDB)
        .where(
            ScanDispatchDB.scan_job_id == scan_id,
            ScanDispatchDB.state == "claimed",
            ScanDispatchDB.claimed_by == worker_id,
            ScanDispatchDB.claim_expires_at >= now,
        )
        .values(
            claim_expires_at=now + ttl,
            heartbeat_at=now,
            updated_at=now,
        )
    )
    return result.rowcount == 1


def renew_claim(control: Control) -> bool:
    """Atomically renew both durable ownership records for a running worker."""
    if control.scan_id is None:
        return False
    now = datetime.now(timezone.utc)
    ttl = timedelta(seconds=control.timeout + 60)
    with SessionLocal() as db:
        lease_ok = heartbeat_lease(
            db, control.scan_id, control.worker_id, now=now, ttl=ttl
        )
        dispatch_ok = heartbeat_dispatch(
            db, control.scan_id, control.worker_id, now, ttl
        )
        if not lease_ok or not dispatch_ok:
            db.rollback()
            return False
        db.commit()
        return True


def finalize_dispatch(control: Control, status: str) -> None:
    """Close the owned outbox claim after a terminal worker outcome."""
    if control.scan_id is None:
        return
    state = "completed" if status == "completed" else status
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.execute(
            update(ScanDispatchDB)
            .where(
                ScanDispatchDB.scan_job_id == control.scan_id,
                ScanDispatchDB.claimed_by == control.worker_id,
            )
            .values(
                state=state,
                claimed_by=None,
                claim_expires_at=None,
                updated_at=now,
            )
        )
        db.commit()


def dispatch_scan(scan_id: int) -> bool:
    """Best-effort dispatcher wake-up for one durably queued scan."""
    with SessionLocal() as db:
        job = db.get(ScanJobDB, scan_id)
        if job is None or job.status not in {"pending", "queued"}:
            return False
        repo_path = job.repo_path
    try:
        control = reserve(scan_id)
    except HTTPException as exc:
        if exc.status_code == 409:
            return False
        raise
    now = datetime.now(timezone.utc)
    ttl = timedelta(seconds=control.timeout + 60)
    with SessionLocal() as db:
        if not claim_dispatch(db, scan_id, control.worker_id, now, ttl):
            db.rollback()
            release(control)
            return False
        dispatch = (
            db.query(ScanDispatchDB)
            .filter(ScanDispatchDB.scan_job_id == scan_id)
            .one()
        )
        job = db.get(ScanJobDB, scan_id)
        if job is None:
            db.rollback()
            release(control)
            return False
        control.version = dispatch.version
        control.durable_claim = True
        job.status = "running"
        job.started_at = now
        db.commit()
    supervise(repo_path, control)
    return True


def reserve(scan_id: int | None = None) -> Control:
    global _active
    try:
        timeout = positive_int('ECDAT_SCAN_TIMEOUT_SECONDS', 300, 3600)
        max_file_bytes()
        positive_int('ECDAT_MAX_SCAN_FILES', 100000, 1000000)
    except ValueError:
        raise HTTPException(503, 'Invalid scan limit configuration') from None
    with _lock:
        if _active is not None:
            raise HTTPException(409, 'A scan is already active on this server')
        _active = Control(timeout, scan_id)
    # Attempt DB lease acquisition when we know the job id.
    if scan_id is not None:
        try:
            with SessionLocal() as db:
                if not acquire_lease(db, scan_id, worker_id=_active.worker_id):
                    release(_active)
                    raise HTTPException(409, 'Scan job already has an active worker lease')
                db.commit()
        except HTTPException:
            raise
        except Exception:
            logger.exception("Lease acquisition failed")
            release(_active)
            raise HTTPException(503, 'Unable to acquire scan lease') from None
    return _active


def claim(control: Control, scan_id: int) -> None:
    """Attach a persisted job to a reserved slot and acquire its durable lease."""
    with _lock:
        if _active is not control:
            raise HTTPException(409, 'Scan slot is no longer active')
        control.scan_id = scan_id
    try:
        with SessionLocal() as db:
            if not acquire_lease(db, scan_id, worker_id=control.worker_id):
                raise HTTPException(409, 'Scan job already has an active worker lease')
            db.commit()
    except Exception:
        release(control)
        raise


def release(control: Control) -> None:
    global _active
    with _lock:
        if _active is not control:
            return
        _active = None
    # Release DB lease if we hold one.
    if control.scan_id is not None:
        try:
            with SessionLocal() as db:
                release_lease(db, control.scan_id, control.worker_id)
                db.commit()
        except Exception:
            logger.exception("Lease release failed")


def request_cancel(scan_id: int) -> None:
    local_signal_sent = False
    with _lock:
        if _active is not None and _active.scan_id == scan_id:
            _active.cancel.set()
            local_signal_sent = True

    with SessionLocal() as db:
        dispatch = (
            db.query(ScanDispatchDB)
            .filter(ScanDispatchDB.scan_job_id == scan_id)
            .first()
        )
        if dispatch is not None and dispatch.state in {
            "pending",
            "claimed",
            "cancel_requested",
        }:
            now = datetime.now(timezone.utc)
            dispatch.state = "cancel_requested"
            dispatch.cancellation_requested_at = dispatch.cancellation_requested_at or now
            dispatch.updated_at = now
            job = db.get(ScanJobDB, scan_id)
            if job is not None and job.status in {"pending", "queued"}:
                job.status = "cancelled"
                job.finished_at = now
                job.duration_ms = 0
                job.blind_spots = ["Scan cancelled before execution"]
                dispatch.state = "cancelled"
            db.commit()
            if job is not None and job.status == "running":
                try:
                    signal_path = _cancel_signal(scan_id)
                    signal_path.parent.mkdir(parents=True, exist_ok=True)
                    signal_path.touch()
                except OSError:
                    logger.exception("Unable to write worker cancellation signal")
                try:
                    reconcile_abandoned_scans()
                except Exception:
                    logger.exception("Unable to reconcile scan after cancellation request")
            return
    if not local_signal_sent:
        raise HTTPException(409, 'Scan is not active')


def cancellation_requested(scan_id: int) -> bool:
    """Return the durable cancellation flag visible to every API replica."""
    with SessionLocal() as db:
        dispatch = (
            db.query(ScanDispatchDB.cancellation_requested_at)
            .filter(ScanDispatchDB.scan_job_id == scan_id)
            .first()
        )
        return dispatch is not None and dispatch[0] is not None


def _finish_if_active(scan_id: int, status: str, message: str) -> None:
    with SessionLocal() as db:
        job = db.get(ScanJobDB, scan_id)
        if job is not None and job.status in {'pending', 'queued', 'running'}:
            was_running = job.status == 'running'
            job.status = status
            job.finished_at = datetime.now(timezone.utc)
            if was_running and job.started_at is not None:
                started = job.started_at
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                job.duration_ms = max(0, round((job.finished_at - started).total_seconds() * 1000))
            else:
                job.duration_ms = 0
            job.blind_spots = [message]
            db.commit()
            logger.info("Scan job finished", extra={"extra_data": {"scan_id": scan_id, "status": status}})
            try:
                record_audit("scan.finished", f"scan:{scan_id}", "system", {"status": status})
            except Exception:
                pass  # audit failures must not break job cleanup


def worker_command(repo_path: str, result_path: Path, progress_path: Path) -> list[str]:
    return [
        sys.executable, '-m', 'backend.scan_worker', repo_path,
        str(result_path), str(progress_path),
    ]


def reconcile_abandoned_scans(*, now: datetime | None = None) -> int:
    """Close jobs whose supervisor stopped heartbeating or exceeded its deadline."""
    from backend.models.scan_lease import ScanLeaseDB

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=SUPERVISOR_GRACE_SECONDS)
    timeout = positive_int('ECDAT_SCAN_TIMEOUT_SECONDS', 300, 3600)
    count = 0
    with SessionLocal() as db:
        active = (
            db.query(ScanJobDB, ScanDispatchDB)
            .join(ScanDispatchDB, ScanDispatchDB.scan_job_id == ScanJobDB.id)
            .filter(ScanJobDB.status == 'running')
            .all()
        )
        for job, dispatch in active:
            started = job.started_at
            if started is None:
                continue
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            heartbeat = dispatch.heartbeat_at
            if heartbeat is not None and heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=timezone.utc)
            overdue = now - started >= timedelta(seconds=timeout + SUPERVISOR_GRACE_SECONDS)
            abandoned = heartbeat is None or heartbeat < cutoff
            if not abandoned:
                continue
            status = ('cancelled' if dispatch.cancellation_requested_at is not None
                      else 'timed_out' if overdue else 'failed')
            message = ('Scan cancelled; results are incomplete' if status == 'cancelled'
                       else 'Scan exceeded its time limit; results are incomplete' if status == 'timed_out'
                       else 'Scan supervisor stopped responding; results are incomplete')
            # The worker also watches the supervisor heartbeat and cancellation signal.
            # A stale process must not be allowed to persist a result after this transition.
            job.status = status
            job.finished_at = now
            job.duration_ms = max(0, round((now - started).total_seconds() * 1000))
            job.blind_spots = [message]
            dispatch.state = status
            dispatch.claimed_by = None
            dispatch.claim_expires_at = None
            dispatch.updated_at = now
            for lease in db.query(ScanLeaseDB).filter(
                ScanLeaseDB.scan_job_id == job.id, ScanLeaseDB.released.is_(False)
            ):
                lease.released = True
                lease.worker_id = ''
            count += 1
        if count:
            db.commit()
    return count


_WORKER_ENV_KEYS = {
    "ECDAT_CORRELATOR_VERSION",
    "ECDAT_DEFAULT_THREAT_HORIZON_YEARS",
    "ECDAT_MAX_EVIDENCE",
    "ECDAT_MAX_FILE_BYTES",
    "ECDAT_MAX_SCAN_FILES",
    "ECDAT_MAX_WORKER_RESULT_BYTES",
    "ECDAT_SCAN_DURATION_BUDGET_MS",
    "ECDAT_SCAN_MEMORY_BUDGET_MB",
    "ECDAT_SCAN_PROFILE",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
    "WINDIR",
}


def worker_environment(workspace: Path) -> dict[str, str]:
    """Build the scanner's minimal environment without control-plane secrets."""
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() in _WORKER_ENV_KEYS
    }
    workspace_value = str(workspace)
    environment.update({
        "HOME": workspace_value,
        "TMP": workspace_value,
        "TEMP": workspace_value,
        "TMPDIR": workspace_value,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    })
    return environment


def _worker_result_limit() -> int:
    return positive_int("ECDAT_MAX_WORKER_RESULT_BYTES", 32 * 1024 * 1024, 128 * 1024 * 1024)


def _worker_diagnostic(path: Path) -> str:
    """Return one bounded diagnostic line without exposing the full traceback."""
    if not path.is_file():
        return ""
    lines = path.read_bytes()[-4096:].decode("utf-8", errors="replace").splitlines()
    return next((line.strip()[:512] for line in reversed(lines) if line.strip()), "")


def _persist_worker_progress(scan_id: int, progress_path: Path) -> None:
    """Copy a bounded progress artifact into the job visible to SSE clients."""
    if not progress_path.is_file() or progress_path.stat().st_size > 64 * 1024:
        return
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    if not isinstance(progress, dict):
        return
    allowed = {
        key: value for key, value in progress.items()
        if key in {"ast", "rule", "dep", "cert", "_phase", "_files_discovered",
                   "_files_supported", "_files_processed", "_files_total", "_findings_count"}
        and ((key == "_phase" and value in {"indexing", "collecting", "correlating", "persisting"})
             or (key != "_phase" and isinstance(value, int) and value >= 0))
    }
    with SessionLocal() as db:
        job = db.get(ScanJobDB, scan_id)
        if job is None or job.status != "running":
            return
        job.collector_stats = allowed
        job.total_files = int(allowed.get("_files_discovered", job.total_files or 0))
        job.in_scope_files = int(allowed.get("_files_supported", allowed.get("_files_total", job.in_scope_files or 0)))
        job.scanned_files = int(allowed.get("_files_processed", job.scanned_files or 0))
        db.commit()


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Terminate the complete worker process group, then confirm its exit."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        # taskkill /T is the Windows-supported process-tree operation. Avoid a
        # shell so the PID cannot be interpreted as command text.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    process.wait(timeout=10)


def supervise(repo_path: str, control: Control) -> None:
    process = None
    status, message = 'failed', 'Scan worker failed; results are incomplete'
    durable_claim = getattr(control, "durable_claim", False) is True
    try:
        if control.cancel.is_set() or (
            durable_claim and cancellation_requested(control.scan_id)
        ):
            status, message = 'cancelled', 'Scan cancelled before execution'
            return
        with tempfile.TemporaryDirectory(prefix=f"ecdat-scan-{control.scan_id}-") as workspace:
            workspace_path = Path(workspace)
            result_path = workspace_path / "result.json"
            progress_path = workspace_path / "progress.json"
            supervisor_heartbeat = workspace_path / "supervisor.heartbeat"
            supervisor_heartbeat.touch()
            cancel_signal = _cancel_signal(control.scan_id)
            cancel_signal.unlink(missing_ok=True)
            diagnostic_dir = Path(__file__).resolve().parents[2] / ".runtime"
            diagnostic_dir.mkdir(parents=True, exist_ok=True)
            stderr_path = diagnostic_dir / f"scan-worker-{control.scan_id}.stderr.log"
            stderr_path.write_bytes(b"")
            progress_mtime = 0
            started = time.monotonic()
            heartbeat_interval = max(1.0, min(10.0, control.timeout / 3))
            next_heartbeat = started + heartbeat_interval
            next_file_heartbeat = started + 2
            popen_kwargs = {
                "cwd": str(Path(__file__).resolve().parents[2]),
                "env": worker_environment(workspace_path),
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = (
                    subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                popen_kwargs["start_new_session"] = True
            for attempt in range(2):
                worker_stderr = stderr_path.open("ab")
                try:
                    process = subprocess.Popen(
                        worker_command(repo_path, result_path, progress_path)
                        + [str(supervisor_heartbeat), str(cancel_signal)],
                        stderr=worker_stderr,
                        **popen_kwargs,
                    )
                finally:
                    worker_stderr.close()
                while process.poll() is None:
                    if time.monotonic() >= next_file_heartbeat:
                        supervisor_heartbeat.touch()
                        next_file_heartbeat = time.monotonic() + 2
                    if control.cancel.wait(0.05) or (
                        durable_claim and cancellation_requested(control.scan_id)
                    ):
                        status, message = 'cancelled', 'Scan cancelled; results are incomplete'
                        break
                    if time.monotonic() >= next_heartbeat:
                        if durable_claim and not renew_claim(control):
                            status, message = 'failed', 'Scan worker lost its execution lease'
                            break
                        next_heartbeat = time.monotonic() + heartbeat_interval
                    if progress_path.is_file():
                        current_mtime = progress_path.stat().st_mtime_ns
                        if current_mtime != progress_mtime:
                            try:
                                _persist_worker_progress(control.scan_id, progress_path)
                            except Exception:
                                # Live progress is best-effort telemetry. A transient
                                # database lock or partial read must never terminate
                                # the isolated scan worker or discard its final result.
                                logger.warning(
                                    "Unable to persist scan progress",
                                    extra={"extra_data": {"scan_id": control.scan_id}},
                                )
                            progress_mtime = current_mtime
                    if time.monotonic() - started >= control.timeout:
                        status, message = 'timed_out', 'Scan exceeded its time limit; results are incomplete'
                        break
                else:
                    if process.returncode != 0 and attempt == 0:
                        process.wait(timeout=10)
                        logger.warning(
                            "Scan worker exited unexpectedly; retrying once",
                            extra={"extra_data": {
                                "scan_id": control.scan_id,
                                "returncode": process.returncode,
                            }},
                        )
                        continue
                    if process.returncode == 0:
                        if progress_path.is_file():
                            try:
                                _persist_worker_progress(control.scan_id, progress_path)
                            except Exception:
                                logger.warning("Unable to persist final scan progress",
                                               extra={"extra_data": {"scan_id": control.scan_id}})
                        if not result_path.is_file() or result_path.stat().st_size > _worker_result_limit():
                            raise ValueError("Scan worker returned an invalid result artifact")
                        from backend.services.scanner_runner import persist_scan_result

                        result = json.loads(result_path.read_text(encoding="utf-8"))
                        persist_scan_result(
                            control.scan_id,
                            result,
                            result_version=getattr(control, "version", 1),
                            session_factory=SessionLocal,
                        )
                        status, message = 'completed', 'Scan completed successfully'
                    else:
                        status = 'failed'
                        message = (
                            f'Scan worker exited unexpectedly (code {process.returncode}); '
                            'results are incomplete'
                        )
                        logger.error(
                            "Scan worker failed after retry: %s",
                            _worker_diagnostic(stderr_path) or "no Python diagnostic",
                            extra={"extra_data": {
                                "scan_id": control.scan_id,
                                "returncode": process.returncode,
                                "diagnostic": _worker_diagnostic(stderr_path),
                            }},
                        )
                    break
                if process.poll() is None:
                    break
    except Exception:
        # Do not expose command paths, inherited configuration, or source text.
        logger.exception("Scan worker supervision failed", extra={"extra_data": {"scan_id": control.scan_id}})
        status, message = 'failed', 'Unable to run scan worker; results are incomplete'
    finally:
        try:
            try:
                if process is not None:
                    if process.poll() is None:
                        _terminate_process_tree(process)
                    else:
                        process.wait(timeout=10)
            except Exception:
                status = 'failed'
                message = 'Unable to confirm scan worker termination; results are incomplete'
            finally:
                _finish_if_active(control.scan_id, status, message)
                if durable_claim:
                    finalize_dispatch(control, status)
        finally:
            release(control)
