# Rate Limiter Service (FastAPI + Redis)

A small standalone **rate limiting** microservice for internal and edge-facing APIs.

It provides:
- **Token Bucket** limits (atomic, Redis Lua script) — good for requests-per-second / requests-per-minute with bursts.
- Optional **Concurrency Leases** — caps *simultaneous* in-flight work for expensive endpoints (uploads, media processing, paid providers).

This is designed to sit in front of services like an S3-compatible proxy (`/proxy`) or media/image analysis endpoints (`/analyze`), where uncontrolled traffic can burn money or melt CPU.

---

## Why a separate service?

- Centralized rules and auditing for multiple microservices
- Keeps application code thin (call the limiter, return `429` if blocked)
- Redis-backed atomicity and consistent behavior across replicas
- Easy to put behind an API gateway / Ingress Controller and still keep per-route rules here

---

## Core Concepts

### Token Bucket
Each key has a “bucket” of tokens:
- **limit**: how many tokens are refilled per `period_seconds`
- **burst**: bucket capacity (how many you can spend instantly)
- **cost**: how many tokens a single request consumes (default `1.0`)

If the bucket doesn’t have enough tokens, the request is denied and you get `retry_after_ms`.

### Concurrency Leases
For heavy endpoints, request-rate limiting is not enough. Concurrency leases cap:
- “Only N jobs can run at the same time per key (and optionally per route).”
- A lease has a **time-to-live (TTL)** so crashes don’t leak slots forever.

---

## Request Identity (“key”)

The limiter needs a **key** that represents the caller identity:
- Preferred: **API key** / **Service ID**
- Fallback: **Client IP** (only if enabled; can be unfair behind Network Address Translation)

Resolution order:
1. `AllowRequest.key` in the JSON body (best, explicit)
2. `X-Api-Key` header
3. `X-Service-Id` header
4. `ip:<client-ip>` if `FALLBACK_TO_IP=true`

---

## Policy Rules

Policies are loaded from a JSON file (`POLICY_PATH`).

Example structure:

```json
{
  "default": {
    "limit": 120,
    "period_seconds": 60,
    "burst": 120,
    "scope": "key_route"
  },
  "rules": [
    {
      "name": "s3_proxy_write",
      "methods": ["PUT", "POST", "DELETE"],
      "path_prefix": "/proxy",
      "limit": 10,
      "period_seconds": 1,
      "burst": 20,
      "scope": "key_route"
    },
    {
      "name": "heavy_analyze",
      "methods": ["POST"],
      "path_prefix": "/analyze",
      "limit": 6,
      "period_seconds": 60,
      "burst": 6,
      "scope": "key_route",
      "concurrency": { "limit": 2, "ttl_seconds": 120 }
    }
  ],
  "bypass_keys": ["internal-admin"]
}
```

### `scope`
- `key` — limits per key, regardless of route
- `key_route` — limits per key + HTTP method + path

---

## API

### `GET /healthz`
Simple health check.

### `POST /v1/allow`
Token bucket decision (and optional concurrency decision).

**Request**
```json
{
  "key": "svc:video-worker",
  "method": "PUT",
  "path": "/proxy/bucket/object",
  "cost": 1.0,
  "tags": {}
}
```

**Response**
```json
{
  "allowed": true,
  "policy": "s3_proxy_write",
  "limit": 10,
  "period_seconds": 1,
  "burst": 20,
  "remaining_tokens": 17.0,
  "retry_after_ms": null,
  "reset_after_ms": 300,
  "concurrency_allowed": null,
  "concurrency_limit": null,
  "lease_id": null,
  "lease_ttl_seconds": null
}
```

**Important note about concurrency inside `/v1/allow`**
If a rule has `concurrency`, the current implementation applies token bucket first and concurrency second.
That can “spend” a token even if concurrency denies the request.

If you need strict correctness for concurrency, use the dedicated lease endpoints below.

### `POST /v1/lease/acquire`
Strict concurrency gate: acquire a lease before doing expensive work.

**Request**
```json
{
  "key": "svc:analyzer",
  "method": "POST",
  "path": "/analyze",
  "ttl_seconds": 120
}
```

**Response**
```json
{
  "allowed": true,
  "lease_id": "ab12cd34...",
  "lease_ttl_seconds": 120,
  "limit": 2,
  "retry_after_ms": null
}
```

### `POST /v1/lease/release`
Release a lease when work finishes.

**Request**
```json
{
  "lease_id": "ab12cd34...",
  "key": "svc:analyzer",
  "method": "POST",
  "path": "/analyze"
}
```

**Response**
```json
{ "released": true }
```

---

## Authentication

Optional shared-secret authentication:
- Set `AUTH_TOKEN` to a non-empty value
- Send header: `X-RL-Auth: <AUTH_TOKEN>`

If `AUTH_TOKEN` is empty, no auth is enforced.

**Production advice**
- Run this service only on internal networks whenever possible
- Prefer **mutual Transport Layer Security (mTLS)** or an internal gateway
- Do not expose Redis publicly

---

## Configuration (Environment Variables)

| Variable | Default | Description |
|---|---:|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `AUTH_TOKEN` | empty | Shared secret for `X-RL-Auth` |
| `POLICY_PATH` | `policies.json` | Policy file path inside the container |
| `KEY_PREFIX` | `rl` | Prefix for Redis keys |
| `FALLBACK_TO_IP` | `true` | Use client IP as key if no explicit key is provided |
| `TRUST_X_FORWARDED_FOR` | `true` | Use `X-Forwarded-For` to determine client IP |

---

## Running locally (Docker Compose)

1. Copy examples:
```bash
cp .env.example .env
cp policies.example.json policies.json
```

2. Start:
```bash
docker compose up --build
```

3. Test:
```bash
curl -X POST http://localhost:8080/v1/allow \
  -H 'Content-Type: application/json' \
  -H 'X-RL-Auth: change-me' \
  -d '{"key":"svc:test","method":"GET","path":"/proxy/demo","cost":1}'
```

---

## Integration Patterns

### Pattern A — Per-request limiting (simple)
Your service calls `/v1/allow` before executing the handler.

If denied, return:
- HTTP status `429 Too Many Requests`
- `Retry-After` header based on `retry_after_ms` (rounded up to seconds)

**Python example (async)**
```python
import httpx
import math

async def check_rate_limit(key: str, method: str, path: str) -> None:
    async with httpx.AsyncClient(timeout=1.0) as client:
        r = await client.post(
            "http://rate-limiter:8080/v1/allow",
            headers={"X-RL-Auth": "change-me"},
            json={"key": key, "method": method, "path": path, "cost": 1.0},
        )
        r.raise_for_status()
        data = r.json()

    if not data["allowed"]:
        retry_ms = data.get("retry_after_ms") or 1000
        retry_s = max(1, math.ceil(retry_ms / 1000))
        raise RuntimeError(f"Rate limited. Retry after {retry_s}s")
```

### Pattern B — Concurrency leases (for heavy jobs)
Use this when you have expensive in-flight work (file encryption, large uploads, image/video analysis, paid APIs).

Flow:
1. `lease/acquire`
2. run job
3. `lease/release` in `finally`

---

## Redis Keys & Expiration

- Token bucket state is stored as a Redis hash:
  - fields: `tokens`, `ts` (timestamp)
  - auto-expire is set to 10 minutes (configurable in code if needed)

- Concurrency leases are stored in a Redis sorted set:
  - member: `lease_id`
  - score: timestamp
  - old entries are purged based on TTL

---

## Operational Notes

### Failure Modes
- If Redis is down: requests will fail (you should decide whether your caller fails open or closed).
  - For expensive endpoints, **fail closed** is safer.
  - For internal “must stay up” endpoints, you might prefer **fail open** plus a hard concurrency cap in-process.

### Observability
- Add access logs at your gateway
- Consider exporting metrics (Prometheus) if you want dashboards (not included in this minimal build)
