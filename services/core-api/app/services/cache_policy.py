"""Which downstream reads core-api may answer from Redis, and for how long.

Opt-in by path. A GET with no entry here is always fetched live, which is the
right default for anything new: ``/health``, ingest status and the like must
never be served from memory.

Each entry names the data domains its answer is built from. A write in any of
them (see ``f1_common.cache.invalidates``) retires the cached answer at once;
the TTL is the backstop for what a write cannot announce — chiefly the clock.
``/forward/next`` changes when a race starts, not when anything is written, so
its TTL is how late the front page may be to roll over.

Only core-api reads through this. The prediction lock scheduler and scoring
call ingestion directly, so the checks that decide whether a forecast may be
published never see a cached answer.
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple

MINUTE = 60
HOUR = 60 * MINUTE


@dataclass(frozen=True)
class Policy:
    ttl_seconds: int
    domains: Tuple[str, ...]


_POLICIES = {
    "ingestion": [
        # Depends on the clock as much as on data: kept short.
        (r"/forward/next", Policy(MINUTE, ("ingestion",))),
        (r"/forward/qualifying-freshness/\d+/\d+", Policy(MINUTE, ("ingestion",))),
        (r"/forward/calendar/\d+", Policy(HOUR, ("ingestion",))),
        (r"/circuits/outlines", Policy(HOUR, ("ingestion",))),
        (r"/circuits/\d+/\d+", Policy(HOUR, ("ingestion",))),
        (r"/data/(seasons|results|practice|qualifying)", Policy(10 * MINUTE, ("ingestion",))),
        (r"/news", Policy(5 * MINUTE, ("news",))),
        # A race week's bounds come from the weekend record, so both domains.
        (r"/news/(weekend/\d+/\d+|search)", Policy(5 * MINUTE, ("news", "ingestion"))),
        (r"/regulations/search", Policy(HOUR, ("regulations",))),
    ],
    "prediction": [
        (r"/predictions/\d+/\d+", Policy(5 * MINUTE, ("prediction",))),
        # Snapshots are immutable once written; the archetype artifact only
        # changes with a deploy, and prediction-service bumps on startup.
        (r"/predictions/snapshot/[^/]+", Policy(24 * HOUR, ("prediction",))),
        (r"/circuits/[^/]+", Policy(24 * HOUR, ("prediction",))),
        # A full Monte Carlo run on every request otherwise. Built from the
        # model and from ingested results, so either moving retires it.
        (r"/championship/\d+", Policy(15 * MINUTE, ("prediction", "ingestion"))),
    ],
    "scoring": [
        (r"/scores", Policy(10 * MINUTE, ("scoring",))),
        (r"/track-record", Policy(10 * MINUTE, ("scoring",))),
    ],
}

_COMPILED = {
    service: [(re.compile(pattern + r"\Z"), policy) for pattern, policy in rules]
    for service, rules in _POLICIES.items()
}


def lookup(service: str, path: str) -> Optional[Policy]:
    for pattern, policy in _COMPILED.get(service, ()):
        if pattern.match(path):
            return policy
    return None
