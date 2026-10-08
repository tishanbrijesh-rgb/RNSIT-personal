"""Local signed-session endpoint used for the ImpactX demonstration."""
import unicodedata

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StrictStr, field_validator

from backend.logging_config import get_logger
from backend.middleware.rate_limit import _client_ip
from backend.middleware.rate_limit import limit as rate_limit
from backend.security import current_role, issue_demo_token

logger = get_logger("ecdat.auth")
router = APIRouter(prefix="/api/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    username: StrictStr = Field(min_length=1, max_length=128)
    password: StrictStr = Field(min_length=1, max_length=1024)

    @field_validator("username", "password")
    @classmethod
    def valid_unicode(cls, value: str) -> str:
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Credentials must contain valid Unicode") from None
        return value


def _login_rate_limit_keys(request: Request, payload: LoginRequest) -> tuple[str, str]:
    username = unicodedata.normalize("NFKC", payload.username).strip().casefold()
    return f"client:{_client_ip(request)}", f"username:{username}"


@router.post("/login")
@rate_limit(window=60, key_fn=_login_rate_limit_keys)
def login(request: Request, payload: LoginRequest) -> dict[str, str | int]:
    logger.info("Login attempt", extra={"extra_data": {"username": payload.username}})
    result = issue_demo_token(payload.username, payload.password)
    logger.info("Login success", extra={"extra_data": {"username": payload.username, "role": result["role"]}})
    return result


@router.get("/me")
def me(role: str = Depends(current_role)) -> dict[str, str]:
    return {"role": role, "authentication": "signed-session"}
