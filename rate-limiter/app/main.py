import logging
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .settings import settings
from .security import require_auth
from .models import (
    AllowRequest,
    AllowResponse,
    LeaseAcquireRequest,
    LeaseAcquireResponse,
    LeaseReleaseRequest,
    LeaseReleaseResponse,
)
from .policy import load_policy, match_rule
from .redis_client import get_redis
from .token_bucket import TOKEN_BUCKET_LUA, make_bucket_key, compute_rate, now_ms as tb_now_ms
from .concurrency import (
    CONC_ACQUIRE_LUA,
    CONC_RELEASE_LUA,
    make_conc_key,
    new_lease_id,
    now_ms as conc_now_ms,
)


log = logging.getLogger("rate-limiter")

app = FastAPI(title=settings.app_name)


@app.on_event("startup")
async def startup():
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    app.state.policy = load_policy(settings.policy_path)
    r = await get_redis()
    app.state.tb_sha = await r.script_load(TOKEN_BUCKET_LUA)
    app.state.conc_acq_sha = await r.script_load(CONC_ACQUIRE_LUA)
    app.state.conc_rel_sha = await r.script_load(CONC_RELEASE_LUA)
    log.info("Loaded policy and Lua scripts")


def _client_ip(req: Request) -> str:
    if settings.trust_x_forwarded_for:
        xff = req.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return req.client.host if req.client else "unknown"


def _resolve_key(req: Request, body_key: str | None) -> str:
    if body_key:
        return body_key

    k = req.headers.get("x-api-key") or req.headers.get("x-service-id")
    if k:
        return k

    if settings.fallback_to_ip:
        return f"ip:{_client_ip(req)}"

    raise HTTPException(status_code=400, detail="Missing key")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": settings.app_name}


@app.post("/v1/allow", dependencies=[Depends(require_auth)])
async def allow(req: Request, payload: AllowRequest) -> AllowResponse:
    policy = app.state.policy
    key = _resolve_key(req, payload.key)

    if key in policy.bypass_keys:
        return AllowResponse(
            allowed=True,
            policy="bypass",
            limit=0,
            period_seconds=0,
            burst=0,
            remaining_tokens=1e9,
            retry_after_ms=None,
            reset_after_ms=0,
        )

    policy_name, limit, period_seconds, burst, scope, conc_pol = match_rule(policy, payload.method, payload.path)

    # Token bucket check
    r = await get_redis()
    bucket_key = make_bucket_key(policy_name, scope, key, payload.method, payload.path)
    rate = compute_rate(limit=limit, period_seconds=period_seconds)
    n = tb_now_ms()

    # EVALSHA returns strings because decode_responses=True; convert carefully
    res = await r.evalsha(app.state.tb_sha, 1, bucket_key, n, rate, burst, float(payload.cost))
    allowed_tb = bool(int(res[0]))
    tokens_after = float(res[1])
    retry_after_ms = int(float(res[2]))
    reset_after_ms = int(float(res[3]))

    # Optional concurrency lease on top (if policy says so)
    conc_allowed = None
    lease_id = None
    lease_ttl = None

    if conc_pol is not None and allowed_tb:
        zkey = make_conc_key(policy_name, scope, key, payload.method, payload.path)
        lease_id = new_lease_id()
        now = conc_now_ms()
        ttl_ms = conc_pol.ttl_seconds * 1000
        cres = await r.evalsha(app.state.conc_acq_sha, 1, zkey, now, ttl_ms, conc_pol.limit, lease_id)
        conc_allowed = bool(int(cres[0]))
        lease_ttl = conc_pol.ttl_seconds

    final_allowed = allowed_tb and (conc_allowed is None or conc_allowed)

    return AllowResponse(
        allowed=final_allowed,
        policy=policy_name,
        limit=limit,
        period_seconds=period_seconds,
        burst=burst,
        remaining_tokens=max(0.0, tokens_after),
        retry_after_ms=None if final_allowed else retry_after_ms,
        reset_after_ms=reset_after_ms,
        concurrency_allowed=conc_allowed,
        concurrency_limit=conc_pol.limit if conc_pol else None,
        lease_id=lease_id if final_allowed and conc_allowed else None,
        lease_ttl_seconds=lease_ttl if final_allowed and conc_allowed else None,
    )


@app.post("/v1/lease/acquire", dependencies=[Depends(require_auth)])
async def lease_acquire(req: Request, payload: LeaseAcquireRequest) -> LeaseAcquireResponse:
    """
    Strict concurrency limiting. Використовуй для "важких" задач:
    acquire -> робота -> release. Якщо робота помре, TTL сам звільнить слот.
    """
    policy = app.state.policy
    key = _resolve_key(req, payload.key)

    policy_name, limit, period_seconds, burst, scope, conc_pol = match_rule(policy, payload.method, payload.path)

    if conc_pol is None:
        raise HTTPException(status_code=400, detail="No concurrency policy for this route")

    ttl = max(1, int(payload.ttl_seconds))
    r = await get_redis()

    zkey = make_conc_key(policy_name, scope, key, payload.method, payload.path)
    lease_id = new_lease_id()
    now = conc_now_ms()
    ttl_ms = ttl * 1000

    cres = await r.evalsha(app.state.conc_acq_sha, 1, zkey, now, ttl_ms, conc_pol.limit, lease_id)
    allowed = bool(int(cres[0]))

    return LeaseAcquireResponse(
        allowed=allowed,
        lease_id=lease_id if allowed else None,
        lease_ttl_seconds=ttl if allowed else None,
        limit=conc_pol.limit,
        retry_after_ms=None if allowed else 250,
    )


@app.post("/v1/lease/release", dependencies=[Depends(require_auth)])
async def lease_release(req: Request, payload: LeaseReleaseRequest) -> LeaseReleaseResponse:
    policy = app.state.policy
    key = _resolve_key(req, payload.key)

    policy_name, limit, period_seconds, burst, scope, conc_pol = match_rule(policy, payload.method, payload.path)

    if conc_pol is None:
        raise HTTPException(status_code=400, detail="No concurrency policy for this route")

    r = await get_redis()
    zkey = make_conc_key(policy_name, scope, key, payload.method, payload.path)
    rr = await r.evalsha(app.state.conc_rel_sha, 1, zkey, payload.lease_id)
    released = bool(int(rr[0]))
    return LeaseReleaseResponse(released=released)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error")
    return JSONResponse(status_code=500, content={"detail": "Internal error"})
