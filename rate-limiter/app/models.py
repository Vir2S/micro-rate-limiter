from pydantic import BaseModel, Field
from typing import Any, Dict, Optional


class AllowRequest(BaseModel):
    key: Optional[str] = None
    method: str
    path: str
    cost: float = 1.0
    tags: Dict[str, Any] = Field(default_factory=dict)


class AllowResponse(BaseModel):
    allowed: bool
    policy: str

    limit: int
    period_seconds: int
    burst: int

    remaining_tokens: float
    retry_after_ms: int | None = None
    reset_after_ms: int

    # concurrency (optional)
    concurrency_allowed: bool | None = None
    concurrency_limit: int | None = None
    lease_id: str | None = None
    lease_ttl_seconds: int | None = None


class LeaseAcquireRequest(BaseModel):
    key: str
    method: str
    path: str
    ttl_seconds: int = 120


class LeaseAcquireResponse(BaseModel):
    allowed: bool
    lease_id: str | None = None
    lease_ttl_seconds: int | None = None
    limit: int
    retry_after_ms: int | None = None


class LeaseReleaseRequest(BaseModel):
    lease_id: str
    key: str
    method: str
    path: str


class LeaseReleaseResponse(BaseModel):
    released: bool
