"""Sliding-window rate limits for the account endpoints.

In-process and in-memory: core-api runs as one process, and the limits exist
to make password guessing slow, not to meter anything that must survive a
restart. A restart resets the windows, which costs an attacker nothing they
could not get by waiting, and costs a real user nothing at all.

Limits are keyed both by client address and by the email being tried. The
address limit stops one machine spraying many accounts; the email limit stops
many machines (or rotating addresses) hammering one account. Unknown and
known emails are counted alike, so the limit itself cannot reveal which
addresses are registered.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Optional

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Limit:
    attempts: int
    window_seconds: float


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def _window(self, key: str, limit: Limit) -> Deque[float]:
        hits = self._hits[key]
        cutoff = self._clock() - limit.window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return hits

    def retry_after(self, key: str, limit: Limit) -> Optional[int]:
        """Seconds until ``key`` may try again, or ``None`` if it may now."""
        hits = self._window(key, limit)
        if len(hits) < limit.attempts:
            return None
        return max(1, math.ceil(hits[0] + limit.window_seconds - self._clock()))

    def hit(self, key: str) -> None:
        self._hits[key].append(self._clock())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)

    def check(self, checks: Dict[str, Limit]) -> None:
        """Raise 429 if any key is over its limit. Records nothing."""
        waits = [w for k, l in checks.items() if (w := self.retry_after(k, l)) is not None]
        if waits:
            wait = max(waits)
            raise HTTPException(
                status_code=429,
                detail="too many attempts; try again in {} minute{}".format(
                    math.ceil(wait / 60), "" if wait <= 60 else "s"
                ),
                headers={"Retry-After": str(wait)},
            )


#: Per address: generous enough for a household behind one router.
LOGIN_PER_ADDRESS = Limit(attempts=20, window_seconds=15 * 60)
#: Failed logins against one email. A success clears it.
LOGIN_FAILURES_PER_EMAIL = Limit(attempts=5, window_seconds=15 * 60)
REGISTER_PER_ADDRESS = Limit(attempts=5, window_seconds=60 * 60)
RESET_PER_ADDRESS = Limit(attempts=5, window_seconds=60 * 60)
RESET_PER_EMAIL = Limit(attempts=3, window_seconds=60 * 60)

limiter = RateLimiter()


def client_address(request: Request, trust_forwarded_for: bool) -> str:
    """The caller's address. ``X-Forwarded-For`` only when a proxy we run set it:
    trusted from the open internet it would let anyone pick their own key."""
    if trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"
