import hashlib
import time
import uuid
from dataclasses import dataclass
from .settings import settings

# Concurrency script:
# KEYS[1] = zset key
# ARGV[1] = now_ms
# ARGV[2] = ttl_ms
# ARGV[3] = limit
# ARGV[4] = lease_id
# Returns: allowed(0/1), active_count
CONC_ACQUIRE_LUA = r"""
local zkey = KEYS[1]
local now_ms = tonumber(ARGV[1])
local ttl_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local lease_id = ARGV[4]

-- purge expired
redis.call('ZREMRANGEBYSCORE', zkey, '-inf', now_ms - ttl_ms)

local count = redis.call('ZCARD', zkey)
if count < limit then
  redis.call('ZADD', zkey, now_ms, lease_id)
  redis.call('PEXPIRE', zkey, ttl_ms * 2)
  return {1, count + 1}
else
  redis.call('PEXPIRE', zkey, ttl_ms * 2)
  return {0, count}
end
"""

# Release:
# KEYS[1] = zset key
# ARGV[1] = lease_id
# Returns: removed(0/1)
CONC_RELEASE_LUA = r"""
local zkey = KEYS[1]
local lease_id = ARGV[1]
local removed = redis.call('ZREM', zkey, lease_id)
return {removed}
"""


@dataclass(frozen=True)
class ConcurrencyResult:
    allowed: bool
    active: int
    lease_id: str | None = None


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def make_conc_key(policy_name: str, scope: str, key: str, method: str, path: str) -> str:
    if scope == "key":
        raw = f"{policy_name}|{key}"
    else:
        raw = f"{policy_name}|{key}|{method.upper()}|{path}"
    return f"{settings.key_prefix}:conc:{_hash(raw)}"


def new_lease_id() -> str:
    return uuid.uuid4().hex


def now_ms() -> int:
    return int(time.time() * 1000)
