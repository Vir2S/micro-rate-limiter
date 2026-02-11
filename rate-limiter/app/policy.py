import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ConcurrencyPolicy:
    limit: int
    ttl_seconds: int


@dataclass(frozen=True)
class Rule:
    name: str
    methods: List[str]
    path_prefix: str

    limit: int
    period_seconds: int
    burst: int
    scope: str  # "key" | "key_route"
    concurrency: Optional[ConcurrencyPolicy] = None


@dataclass(frozen=True)
class Policy:
    default_limit: int
    default_period_seconds: int
    default_burst: int
    default_scope: str
    rules: List[Rule]
    bypass_keys: set[str]


def _parse_rule(obj: Dict[str, Any]) -> Rule:
    conc = None
    if "concurrency" in obj and obj["concurrency"]:
        c = obj["concurrency"]
        conc = ConcurrencyPolicy(limit=int(c["limit"]), ttl_seconds=int(c.get("ttl_seconds", 120)))

    return Rule(
        name=obj.get("name", "rule"),
        methods=[m.upper() for m in obj.get("methods", ["GET"])],
        path_prefix=obj.get("path_prefix", "/"),
        limit=int(obj["limit"]),
        period_seconds=int(obj["period_seconds"]),
        burst=int(obj.get("burst", obj["limit"])),
        scope=obj.get("scope", "key_route"),
        concurrency=conc,
    )


def load_policy(path: str) -> Policy:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))

    d = data.get("default", {})
    rules = [_parse_rule(r) for r in data.get("rules", [])]

    return Policy(
        default_limit=int(d.get("limit", 60)),
        default_period_seconds=int(d.get("period_seconds", 60)),
        default_burst=int(d.get("burst", d.get("limit", 60))),
        default_scope=d.get("scope", "key_route"),
        rules=rules,
        bypass_keys=set(data.get("bypass_keys", [])),
    )


def match_rule(policy: Policy, method: str, path: str) -> tuple[str, int, int, int, str, ConcurrencyPolicy | None]:
    """
    Returns (policy_name, limit, period, burst, scope, concurrency_policy)
    """
    method = method.upper()

    for r in policy.rules:
        if method in r.methods and path.startswith(r.path_prefix):
            return (r.name, r.limit, r.period_seconds, r.burst, r.scope, r.concurrency)

    return ("default", policy.default_limit, policy.default_period_seconds, policy.default_burst, policy.default_scope, None)
