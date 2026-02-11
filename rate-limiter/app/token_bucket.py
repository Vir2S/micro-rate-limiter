import hashlib
import time
from dataclasses import dataclass
from .settings import settings

# Token bucket script:
# KEYS[1] = bucket key
# ARGV[1] = now_ms
# ARGV[2] = refill_rate_per_ms
# ARGV[3] = capacity
# ARGV[4] = cost
# Returns: allowed(0/1), tokens_after, retry_after_ms, reset_after_ms
TOKEN_BUCKET_LUA = r"""
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local cap = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])

if tokens == nil then tokens = cap end
if ts == nil then ts = now_ms end

local delta = now_ms - ts
if delta < 0 then delta = 0 end

tokens = math.min(cap, tokens + (delta * rate))

local allowed = 0
local retry_after = 0
if tokens >= cost then
  allowed = 1
  tokens = tokens - cost
else
  local need = cost - tokens
  if rate > 0 then
    retry_after = math.ceil(need / rate)
  else
    retry_after = 2147483647
  end
end

-- reset_after: time to refill to cap
local reset_after = 0
if rate > 0 then
  reset_after = math.ceil((cap - tokens) / rate)
else
  reset_after = 2147483647
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now_ms)
-- Keep bucket around for a while; 10 minutes by default.
redis.call('PEXPIRE', key, 600000)

return {allowed, tokens, retry_after, reset_after}
"""


@dataclass(frozen=True)
class BucketResult:
    allowed: bool
    tokens_after: float
    retry_after_ms: int
    reset_after_ms: int


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def make_bucket_key(policy_name: str, scope: str, key: str, method: str, path: str) -> str:
    """
    scope:
      - "key": per key, regardless of route
      - "key_route": per key + method + path group
    """
    if scope == "key":
        raw = f"{policy_name}|{key}"
    else:
        raw = f"{policy_name}|{key}|{method.upper()}|{path}"
    return f"{settings.key_prefix}:tb:{_hash(raw)}"


def now_ms() -> int:
    return int(time.time() * 1000)


def compute_rate(limit: int, period_seconds: int) -> float:
    # tokens per millisecond
    period_ms = max(1, period_seconds * 1000)
    return float(limit) / float(period_ms)
