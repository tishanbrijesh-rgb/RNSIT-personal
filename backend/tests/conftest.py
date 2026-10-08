"""Session-scoped pytest fixtures for ECDAT backend integration tests.

Sets up an isolated in-memory database with all tables and patches every
module-level SessionLocal to point at it.  All patches are restored in
finally blocks so no state leaks between test sessions.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure backend package is importable when running from backend/.
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Environment MUST be set before any backend module reads os.environ.
import tests.integration_env  # noqa: F401

os.environ.setdefault("ECDAT_ALLOW_ROLE_HEADER", "true")
os.environ.setdefault(
    "ECDAT_TOKEN_SECRET",
    "test-secret-key-for-pytest-only-32chars!!",
)
os.environ.setdefault(
    "ECDAT_USERS_JSON",
    (
        '{"testadmin":{"password":"testadmin-password-16c!","role":"admin"},'
        ' "testanalyst":{"password":"testanalyst-password-16!","role":"security_analyst"}}'
    ),
)

# Import backend modules AFTER env is set.
import backend.db as _db
import backend.middleware.rate_limit as _rl
import backend.models.audit_log as _audit_log_mod  # registers audit table  # noqa: F401
import backend.models.scan_failure as _scan_failure_mod  # registers scan failure table  # noqa: F401
import backend.models.scan_lease as _scan_lease_mod  # registers scan lease table  # noqa: F401
import backend.routers.assets as _assets_mod
import backend.routers.dashboard as _dash_mod
import backend.routers.outputs as _outputs_mod
import backend.routers.scan as _scan_mod
import backend.security as _sec
import backend.services.scan_control as _sc
from backend.db import Base
from backend.main import app
from backend.security import current_role

# ── Capture original references BEFORE any patching ──────────────────────────

_ORIG_SESSIONLOCALS = {
    "backend.db": _db.SessionLocal,
    "backend.routers.scan": _scan_mod.SessionLocal,
    "backend.routers.assets": _assets_mod.SessionLocal,
    "backend.routers.dashboard": _dash_mod.SessionLocal,
    "backend.routers.outputs": _outputs_mod.SessionLocal,
    "backend.security": _sec.SessionLocal,
    "backend.services.scan_control": _sc.SessionLocal,
}

_MODULES = (
    _db,
    _scan_mod,
    _assets_mod,
    _dash_mod,
    _outputs_mod,
    _sec,
    _sc,
)


@pytest.fixture(scope="session")
def db_engine():
    """Create a fresh in-memory SQLite engine with all tables created.

    All model modules are imported at module load time (above), so their
    tables are registered on ``Base.metadata`` before ``create_all``.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture(scope="session")
def session_factory(db_engine):
    """Return a session factory bound to the test engine."""
    return sessionmaker(bind=db_engine, autocommit=False, autoflush=False)


@pytest.fixture()
def client(session_factory):
    """Return a TestClient wired to the in-memory database with auth mocked.

    All patches are restored in a finally block so failed tests cannot leak state.
    """
    factory = session_factory

    # Patch module-level SessionLocal everywhere it is used directly.
    for mod in _MODULES:
        mod.SessionLocal = factory

    # Override the current_role dependency so endpoints receive "security_analyst".
    app.dependency_overrides[current_role] = lambda: "security_analyst"

    # Disable rate limiter.
    orig_allow = _rl._allow
    _rl._allow = lambda *a, **kw: True

    # Suppress background supervise task (avoids spawning subprocesses).
    supervise_patcher = patch(
        "backend.routers.scan.supervise",
        side_effect=RuntimeError("suppressed in tests"),
    )
    supervise_patcher.start()

    # Silence audit writes.
    audit_patcher = patch("backend.security.record_audit")
    audit_patcher.start()

    # Make background_tasks.add_task a no-op.
    bt_patcher = patch(
        "fastapi.BackgroundTasks.add_task",
        side_effect=lambda *a, **kw: None,
    )
    bt_patcher.start()

    # Keep the watchdog off the fixture's single shared SQLite connection.
    # Dedicated scan admission tests exercise reconciliation directly.
    watchdog_patcher = patch("backend.services.scan_control.reconcile_abandoned_scans", return_value=0)
    watchdog_patcher.start()

    # Reset scan control active slot.
    orig_active = _sc._active
    _sc._active = None

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        # Restore everything in reverse order.
        app.dependency_overrides.clear()
        supervise_patcher.stop()
        audit_patcher.stop()
        bt_patcher.stop()
        watchdog_patcher.stop()
        _rl._allow = orig_allow
        _sc._active = orig_active
        for mod in _MODULES:
            mod.SessionLocal = _ORIG_SESSIONLOCALS[mod.__name__]
