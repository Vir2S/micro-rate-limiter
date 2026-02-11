from fastapi import Header, HTTPException
from .settings import settings


def require_auth(x_rl_auth: str | None = Header(default=None, alias="X-RL-Auth")) -> None:
    """
    Shared-secret auth. If AUTH_TOKEN is empty -> no auth required.
    """
    if not settings.auth_token:
        return
    if not x_rl_auth or x_rl_auth != settings.auth_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
