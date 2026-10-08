"""Signed demo sessions, RBAC, and audit helpers for the local ImpactX deployment."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Self

from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from backend.db import SessionLocal
from backend.logging_config import get_logger
from backend.models.audit_log import AuditLogDB, RevokedSessionDB
from backend.settings import ROLES, SettingsError, get_settings

logger = get_logger("ecdat.security")


class Principal(str):
    """Authenticated identity that remains compatible with legacy role strings."""

    subject: str
    role: str
    expires_at: int
    session_id: str

    def __new__(
        cls,
        subject: str,
        role: str,
        expires_at: int,
        session_id: str,
    ) -> Self:
        principal = super().__new__(cls, role)
        principal.subject = subject
        principal.role = role
        principal.expires_at = expires_at
        principal.session_id = session_id
        return principal


def _settings():
    """Fail closed; provision distinct account credentials outside source control."""
    try:
        settings = get_settings()
        return settings.token_secret, settings.users
    except SettingsError as exc:
        logger.error("Authentication configuration validation failed: %s", exc)
        raise HTTPException(503, "Authentication configuration is missing or invalid") from None


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_demo_token(username: str, password: str) -> dict[str, str | int]:
    """Issue a short-lived HMAC session for one documented local demo account."""
    secret, users = _settings()
    user = users.get(username.lower())
    expected = user.password if user else "invalid-account-password"
    if not hmac.compare_digest(password.encode(), expected.encode()) or user is None:
        raise HTTPException(401, "Invalid demo credentials")
    role = user.role
    expires_at = int(time.time()) + 3600
    session_id = secrets.token_urlsafe(18)
    payload = _encode(
        json.dumps(
            {
                "sub": username.lower(),
                "role": role,
                "exp": expires_at,
                "sid": session_id,
            },
            separators=(",", ":"),
        ).encode()
    )
    signature = _encode(hmac.new(secret, payload.encode(), hashlib.sha256).digest())
    return {"access_token": f"{payload}.{signature}", "token_type": "bearer", "role": role, "expires_at": expires_at}


def role_from_token(token: str) -> Principal:
    secret, users = _settings()
    try:
        if len(token) > 4096:
            raise ValueError("oversized")
        payload, signature = token.split(".", 1)
        expected = _encode(hmac.new(secret, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature.encode(), expected.encode()):
            raise ValueError("signature")
        claims = json.loads(_decode(payload))
        if (
            not isinstance(claims, dict)
            or type(claims.get("exp")) is not int
            or not isinstance(claims.get("sub"), str)
            or not isinstance(claims.get("sid"), str)
            or not claims["sid"]
        ):
            raise ValueError("claims")
        account = users.get(claims["sub"])
        if claims["exp"] <= int(time.time()) or claims["role"] not in ROLES or account is None or account.role != claims["role"]:
            raise ValueError("expired or invalid role")
        principal = Principal(
            subject=claims["sub"],
            role=claims["role"],
            expires_at=claims["exp"],
            session_id=claims["sid"],
        )
        if _is_session_revoked(principal.session_id):
            raise ValueError("revoked session")
        return principal
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(401, "Invalid or expired ECDAT session") from exc


def _is_session_revoked(session_id: str) -> bool:
    db = SessionLocal()
    try:
        return db.get(RevokedSessionDB, session_id) is not None
    finally:
        db.close()


def revoke_session(principal: Principal) -> None:
    """Persistently reject a signed session on all API replicas sharing the database."""
    db = SessionLocal()
    try:
        db.merge(
            RevokedSessionDB(
                session_id=principal.session_id,
                subject=principal.subject,
                expires_at=principal.expires_at,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def current_role(
    authorization: str | None = Header(default=None),
    x_ecdat_role: str | None = Header(default=None),
) -> Principal:
    """Resolve a signed session, with an explicitly configurable demo-header fallback."""
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "Use Authorization: Bearer <token>")
        return role_from_token(token)
    try:
        allow_header = get_settings().allow_role_header
    except SettingsError as exc:
        logger.error("Authentication configuration validation failed: %s", exc)
        raise HTTPException(503, "Authentication configuration is missing or invalid") from None
    if not allow_header:
        raise HTTPException(401, "Authentication required")
    role = (x_ecdat_role or "security_analyst").lower().replace(" ", "_")
    if role not in ROLES:
        raise HTTPException(403, "Unknown ECDAT role")
    return Principal(
        subject=f"demo-header:{role}",
        role=role,
        expires_at=0,
        session_id="demo-header",
    )

def ensure_write_role(role: str) -> None:
    if role not in {"admin", "security_analyst"}:
        raise HTTPException(403, "This action requires Admin or Security Analyst role")

def record_audit(
    action: str,
    resource: str,
    role: Principal | str = "security_analyst",
    details: dict | None = None,
    *,
    session: Session | None = None,
) -> AuditLogDB:
    principal = (
        role
        if isinstance(role, Principal)
        else Principal(
            subject="system" if role == "system" else f"legacy-role:{role}",
            role=role,
            expires_at=0,
            session_id="system" if role == "system" else "legacy",
        )
    )
    owns_session = session is None
    db = session or SessionLocal()
    try:
        event = AuditLogDB(
            actor_subject=principal.subject,
            actor_role=principal.role,
            actor_session_id=principal.session_id,
            actor_expires_at=principal.expires_at,
            action=action,
            resource=resource,
            details=details or {},
        )
        db.add(event)
        if owns_session:
            db.commit()
        return event
    except Exception:
        if owns_session:
            db.rollback()
        raise
    finally:
        if owns_session:
            db.close()
