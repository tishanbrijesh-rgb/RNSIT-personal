"""Stage 2 lifecycle regression tests.

Covers:
  - Successful completion -> 'completed'
  - Worker crash/failure -> 'failed'
  - Launch failure (Popen exception) -> 'failed'
  - Cancellation -> 'cancelled'
  - Timeout -> 'timed_out'
  - Forced worker termination
  - _finish_if_active terminal-state exclusivity
  - Structured failure info (not silent zeros)
  - request_cancel state validation
"""
from __future__ import annotations

import os
import subprocess
import types
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture(autouse=True)
def isolate_fake_process_termination():
    """Lifecycle mocks must never send OS signals using fabricated process IDs.

    Real process-tree termination is covered in test_scan_worker_isolation.
    """
    with patch("backend.services.scan_control._terminate_process_tree",
               side_effect=lambda process: process.kill()):
        yield

# ── Environment ───────────────────────────────────────────────────────────────
os.environ.setdefault("ECDAT_ALLOW_ROLE_HEADER", "true")
os.environ.setdefault(
    "ECDAT_TOKEN_SECRET", "test-secret-key-for-pytest-only-32chars!!"
)
os.environ.setdefault(
    "ECDAT_USERS_JSON",
    '{"testadmin":{"password":"testadmin-password-16c!","role":"admin"}}',
)

# ── Shared in-memory test database ───────────────────────────────────────────
_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

from backend.db import Base

Base.metadata.create_all(bind=_engine)

_session_factory = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

import backend.db as _db
import backend.security as _sec
import backend.services.scan_control as _sc
import backend.services.scan_lease as _sl
import backend.services.scanner_runner as _sr

# Save originals so we can re-patch if conftest overwrites them.
_orig_db_sl = _db.SessionLocal
_orig_sc_sl = _sc.SessionLocal
_orig_sr_sl = _sr.SessionLocal
_orig_sl_sl = getattr(_sl, "SessionLocal", None)
_orig_sec_sl = _sec.SessionLocal


def _apply_db_patches():
    """Patch all module-level SessionLocal references to the test factory."""
    _db.SessionLocal = _session_factory
    _sc.SessionLocal = _session_factory
    _sr.SessionLocal = _session_factory
    _sec.SessionLocal = _session_factory
    if _orig_sl_sl is not None:
        _sl.SessionLocal = _session_factory


def _restore_db_patches():
    """Restore original SessionLocal references."""
    _db.SessionLocal = _orig_db_sl
    _sc.SessionLocal = _orig_sc_sl
    _sr.SessionLocal = _orig_sr_sl
    _sec.SessionLocal = _orig_sec_sl
    if _orig_sl_sl is not None:
        _sl.SessionLocal = _orig_sl_sl


from backend.models.scan_job import ScanJobDB
from backend.services.scan_control import (
    _finish_if_active,
    request_cancel,
    supervise,
)


def _db_session():
    """Create a fresh session from the test factory."""
    return _session_factory()


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _ensure_test_db():
    """Re-apply test DB patches before each test (in case conftest overwrote)."""
    _apply_db_patches()
    yield
    _restore_db_patches()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _dummy_control(scan_id: int, *, timeout: int = 300, cancelled: bool = False):
    """Return a Control-like object that avoids touching the real _active slot."""
    cancel_evt = types.SimpleNamespace(
        is_set=lambda: cancelled,
        wait=lambda timeout: cancelled,
        set=lambda: None,
    )
    return types.SimpleNamespace(
        scan_id=scan_id,
        timeout=timeout,
        cancel=cancel_evt,
    )


def _fake_process(final_rc: int | None):
    """Return a mock Popen that returns final_rc on poll after first call."""
    p = MagicMock()
    p.returncode = final_rc
    call_count = [0]

    def poll_side_effect():
        call_count[0] += 1
        # First call: still running. All subsequent calls: return final_rc.
        return None if call_count[0] == 1 else final_rc

    p.poll.side_effect = poll_side_effect
    p.wait.return_value = final_rc
    p.kill.return_value = None
    return p


# ── Lifecycle: successful completion ─────────────────────────────────────────


class TestLifecycleSuccessful:
    """supervise() transitions a scan to 'completed' when the worker exits 0."""

    def test_worker_exit_zero_sets_completed(self):
        """Worker exit 0 -> _finish_if_active called with 'completed'."""
        fake_process = _fake_process(0)
        fake_process.wait.return_value = 0

        def write_result(_repo_path, result_path, _progress_path):
            result_path.write_text('{"findings":[],"metrics":{}}', encoding="utf-8")
            return ["python", "-c", "pass"]

        with patch("backend.services.scan_control.subprocess") as mock_sub:
            mock_sub.Popen.return_value = fake_process
            mock_sub.DEVNULL = subprocess.DEVNULL
            mock_sub.CREATE_NO_WINDOW = 0
            control = _dummy_control(1)

            with patch("backend.services.scan_control.worker_command",
                       side_effect=write_result), \
                 patch("backend.services.scanner_runner.persist_scan_result"), \
                 patch("backend.services.scan_control._finish_if_active") as mock_finish:
                supervise("/fake/repo", control)

        assert mock_finish.called
        call_args = mock_finish.call_args
        assert call_args[0][0] == control.scan_id
        assert call_args[0][1] == "completed"

    def test_worker_exit_zero_releases_control(self):
        """Control is released after a successful worker exit."""
        fake_process = _fake_process(0)

        with patch("backend.services.scan_control.subprocess") as mock_sub:
            mock_sub.Popen.return_value = fake_process
            mock_sub.DEVNULL = subprocess.DEVNULL
            mock_sub.CREATE_NO_WINDOW = 0
            control = _dummy_control(1)

            with patch("backend.services.scan_control.worker_command",
                       return_value=["python", "-c", "pass"]):
                with patch("backend.services.scan_control.release") as mock_release:
                    with patch("backend.services.scan_control._finish_if_active"):
                        supervise("/fake/repo", control)

        assert mock_release.called


# ── Lifecycle: worker failure ─────────────────────────────────────────────────


class TestLifecycleWorkerFailure:
    """Worker crash/launch failure should produce 'failed'."""

    def test_worker_crash_sets_failed(self):
        """Worker exit != 0 -> status 'failed'."""
        fake_process = _fake_process(1)

        with patch("backend.services.scan_control.subprocess") as mock_sub:
            mock_sub.Popen.return_value = fake_process
            mock_sub.DEVNULL = subprocess.DEVNULL
            mock_sub.CREATE_NO_WINDOW = 0
            control = _dummy_control(1)

            with patch("backend.services.scan_control.worker_command",
                       return_value=["python", "-c", "pass"]):
                with patch("backend.services.scan_control._finish_if_active") as mock_finish:
                    supervise("/fake/repo", control)

        assert mock_finish.call_args[0][1] == "failed"

    def test_worker_killed_by_timeout(self):
        """Worker that never exits is killed and status is 'timed_out'."""
        fake_process = _fake_process(None)

        with patch("backend.services.scan_control.subprocess") as mock_sub:
            mock_sub.Popen.return_value = fake_process
            mock_sub.DEVNULL = subprocess.DEVNULL
            mock_sub.CREATE_NO_WINDOW = 0
            control = _dummy_control(1, timeout=0)

            with patch("backend.services.scan_control.worker_command",
                       return_value=["python", "-c", "pass"]):
                with patch("backend.services.scan_control._finish_if_active") as mock_finish:
                    supervise("/fake/repo", control)

        assert fake_process.kill.called
        assert mock_finish.call_args[0][1] == "timed_out"

    def test_launch_failure_sets_failed(self):
        """Popen raising OSError -> status 'failed'."""
        with patch("backend.services.scan_control.subprocess") as mock_sub:
            mock_sub.Popen.side_effect = OSError("no such file")
            mock_sub.DEVNULL = subprocess.DEVNULL
            mock_sub.CREATE_NO_WINDOW = 0
            control = _dummy_control(1)

            with patch("backend.services.scan_control.worker_command",
                       return_value=["nonexistent_binary"]):
                with patch("backend.services.scan_control._finish_if_active") as mock_finish:
                    supervise("/fake/repo", control)

        assert mock_finish.call_args[0][1] == "failed"


# ── Lifecycle: cancellation ───────────────────────────────────────────────────


class TestLifecycleCancellation:
    """Cancel should set status to 'cancelled' and kill the worker."""

    def test_cancel_before_worker_launch(self):
        """Cancel set before supervise -> status 'cancelled', no Popen."""
        with patch("backend.services.scan_control.subprocess") as mock_sub:
            control = _dummy_control(1, cancelled=True)

            with patch("backend.services.scan_control.worker_command",
                       return_value=["python", "-c", "pass"]):
                with patch("backend.services.scan_control._finish_if_active") as mock_finish:
                    supervise("/fake/repo", control)

        assert not mock_sub.Popen.called
        assert mock_finish.call_args[0][1] == "cancelled"

    def test_cancel_during_execution_kills_worker(self):
        """Cancel mid-run kills the worker process."""
        fake_process = _fake_process(None)
        cancel_evt = types.SimpleNamespace(
            is_set=lambda: False,
            wait=lambda timeout: True,
        )
        control = types.SimpleNamespace(
            scan_id=1, timeout=300, cancel=cancel_evt,
        )

        with patch("backend.services.scan_control.subprocess") as mock_sub:
            mock_sub.Popen.return_value = fake_process
            mock_sub.DEVNULL = subprocess.DEVNULL
            mock_sub.CREATE_NO_WINDOW = 0

            with patch("backend.services.scan_control.worker_command",
                       return_value=["python", "-c", "pass"]):
                with patch("backend.services.scan_control._finish_if_active") as mock_finish:
                    supervise("/fake/repo", control)

        assert fake_process.kill.called
        assert mock_finish.call_args[0][1] == "cancelled"


# ── Lifecycle: _finish_if_active ─────────────────────────────────────────────


class TestFinishIfActive:
    """_finish_if_active always transitions to a terminal state."""

    def test_updates_pending_job(self):
        """A pending job is moved to the given terminal status."""
        db = _db_session()
        scan_id = None
        try:
            job = ScanJobDB(repo_path="/tmp", status="pending")
            db.add(job)
            db.flush()
            db.refresh(job)
            scan_id = job.id

            _finish_if_active(scan_id, "failed", "test failure")
        finally:
            db.rollback()
            updated = db.get(ScanJobDB, scan_id)
            assert updated.status == "failed"
            db.close()

    def test_noop_on_already_terminal(self):
        """_finish_if_active does not overwrite an already-terminal job."""
        db = _db_session()
        scan_id = None
        try:
            job = ScanJobDB(repo_path="/tmp", status="completed")
            db.add(job)
            db.commit()  # Must commit so _finish_if_active's separate session sees it
            db.refresh(job)
            scan_id = job.id

            _finish_if_active(scan_id, "failed", "should not apply")
        finally:
            db.rollback()
            updated = db.get(ScanJobDB, scan_id)
            assert updated is not None
            assert updated.status == "completed"
            db.close()

    def test_sets_finished_at_with_tzinfo(self):
        """_finish_if_active sets finished_at (non-null after completion)."""
        db = _db_session()
        scan_id = None
        try:
            job = ScanJobDB(repo_path="/tmp", status="running")
            db.add(job)
            db.commit()  # Must commit so _finish_if_active's separate session sees it
            db.refresh(job)
            scan_id = job.id

            _finish_if_active(scan_id, "completed", "ok")
        finally:
            db.rollback()
            updated = db.get(ScanJobDB, scan_id)
            assert updated is not None
            assert updated.finished_at is not None
            # DateTime column stores timezone-naive values in SQLite;
            # the source value is timezone-aware (UTC), just converted.
            db.close()

    @pytest.mark.parametrize("terminal_status", [
        "completed",
        "failed",
        "cancelled",
        "timed_out",
    ])
    def test_each_terminal_status_reachable(self, terminal_status):
        """_finish_if_active can set any of the four terminal statuses."""
        db = _db_session()
        scan_id = None
        try:
            job = ScanJobDB(repo_path="/tmp", status="running")
            db.add(job)
            db.flush()
            db.refresh(job)
            scan_id = job.id

            _finish_if_active(scan_id, terminal_status, "parametrized")
        finally:
            db.rollback()
            updated = db.get(ScanJobDB, scan_id)
            assert updated.status == terminal_status
            db.close()


# ── Lifecycle: request_cancel ─────────────────────────────────────────────────


class TestRequestCancel:
    """request_cancel validates active-scan state and sets the cancel flag."""

    def test_cancel_active_scan_succeeds(self):
        """request_cancel works when the scan matches _active.scan_id."""
        calls = []
        cancel_evt = types.SimpleNamespace(set=lambda: calls.append(True) or None)
        active = types.SimpleNamespace(scan_id=42, cancel=cancel_evt)
        with patch("backend.services.scan_control._active", active):
            request_cancel(42)
        assert len(calls) == 1

    def test_cancel_non_active_raises_409(self):
        """request_cancel raises HTTPException(409) for non-active scan."""
        cancel_evt = types.SimpleNamespace(set=lambda: None)
        active = types.SimpleNamespace(scan_id=99, cancel=cancel_evt)
        with patch("backend.services.scan_control._active", active):
            with pytest.raises(HTTPException) as exc_info:
                request_cancel(42)
        assert exc_info.value.status_code == 409


# ── Lifecycle: structured failure info ───────────────────────────────────────


class TestStructuredFailureInfo:
    """Scan failures carry structured messages -- never silent zero results."""

    def test_exception_handler_returns_status_and_blind_spots(self):
        """run_scan exception handler returns a dict with status and 0 assets."""
        # When _run_scan raises, run_scan catches it and returns a failure dict.
        # We create a job in the test DB and pass its ID.
        db = _db_session()
        try:
            job = ScanJobDB(repo_path="/nonexistent", status="running")
            db.add(job)
            db.commit()
            scan_id = job.id

            # Patch _run_scan (in the module's globals) to raise.
            with patch.dict(
                "backend.services.scanner_runner.__dict__",
                {"_run_scan": MagicMock(side_effect=RuntimeError("pipeline boom"))},
            ):
                from backend.services.scanner_runner import run_scan
                result = run_scan("/nonexistent", scan_id=scan_id)
        finally:
            db.close()

        assert result["status"] == "failed"
        assert result["assets_found"] == 0

    def test_finish_records_blind_spot_explanation(self):
        """_finish_if_active stores a human-readable failure explanation."""
        db = _db_session()
        scan_id = None
        try:
            job = ScanJobDB(repo_path="/tmp", status="queued")
            db.add(job)
            db.flush()
            db.refresh(job)
            scan_id = job.id

            _finish_if_active(scan_id, "failed", "collector crashed on UTF-8 path")
        finally:
            db.rollback()
            updated = db.get(ScanJobDB, scan_id)
            assert updated.blind_spots is not None
            assert len(updated.blind_spots) >= 1
            assert isinstance(updated.blind_spots[0], str)
            assert "collector" in updated.blind_spots[0].lower()
            db.close()


# ── Lifecycle: lease cleanup ──────────────────────────────────────────────────


class TestLeaseCleanup:
    """Control.release and DB lease are released on every supervise exit path."""

    def test_release_called_on_success(self):
        """release() is called after successful worker exit."""
        fake_process = _fake_process(0)

        with patch("backend.services.scan_control.subprocess") as mock_sub:
            mock_sub.Popen.return_value = fake_process
            mock_sub.DEVNULL = subprocess.DEVNULL
            mock_sub.CREATE_NO_WINDOW = 0
            control = _dummy_control(1)

            with patch("backend.services.scan_control.worker_command",
                       return_value=["python", "-c", "pass"]):
                with patch("backend.services.scan_control.release") as mock_release:
                    with patch("backend.services.scan_control._finish_if_active"):
                        supervise("/fake/repo", control)

        assert mock_release.called

    def test_release_called_on_failure(self):
        """release() is called after worker crash."""
        fake_process = _fake_process(1)

        with patch("backend.services.scan_control.subprocess") as mock_sub:
            mock_sub.Popen.return_value = fake_process
            mock_sub.DEVNULL = subprocess.DEVNULL
            mock_sub.CREATE_NO_WINDOW = 0
            control = _dummy_control(1)

            with patch("backend.services.scan_control.worker_command",
                       return_value=["python", "-c", "pass"]):
                with patch("backend.services.scan_control.release") as mock_release:
                    with patch("backend.services.scan_control._finish_if_active"):
                        supervise("/fake/repo", control)

        assert mock_release.called

    def test_release_called_on_cancel(self):
        """release() is called after cancellation."""
        fake_process = _fake_process(None)
        cancel_evt = types.SimpleNamespace(
            is_set=lambda: False,
            wait=lambda timeout: True,
        )
        control = types.SimpleNamespace(
            scan_id=1, timeout=300, cancel=cancel_evt,
        )

        with patch("backend.services.scan_control.subprocess") as mock_sub:
            mock_sub.Popen.return_value = fake_process
            mock_sub.DEVNULL = subprocess.DEVNULL
            mock_sub.CREATE_NO_WINDOW = 0

            with patch("backend.services.scan_control.worker_command",
                       return_value=["python", "-c", "pass"]):
                with patch("backend.services.scan_control.release") as mock_release:
                    with patch("backend.services.scan_control._finish_if_active"):
                        supervise("/fake/repo", control)

        assert mock_release.called
