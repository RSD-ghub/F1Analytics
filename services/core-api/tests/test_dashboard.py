"""BFF aggregation tests.

The behaviour worth pinning is what happens when a downstream service is down.
A page that fails entirely because one panel is unavailable turns a partial
outage into a total one — and a page that silently omits the panel is worse,
because the reader cannot tell "no data" from "broken".
"""

import pytest

from app.services.downstream import DownstreamUnavailable, gather_optional


async def _ok(value):
    return value


async def _fails(service="prediction"):
    raise DownstreamUnavailable(service, "connection refused")


# ── gather_optional ──────────────────────────────────────────────────────────


async def test_all_succeeding_returns_everything():
    out = await gather_optional(a=_ok(1), b=_ok(2))

    assert out["a"] == 1
    assert out["b"] == 2
    assert out["_unavailable"] == []


async def test_one_failure_does_not_lose_the_others():
    """The core property: a partial outage stays partial."""
    out = await gather_optional(good=_ok("kept"), bad=_fails())

    assert out["good"] == "kept"
    assert out["bad"] is None
    assert out["_unavailable"] == ["bad"]


async def test_failures_are_named_not_silently_dropped():
    """A missing panel the reader cannot account for is worse than an error."""
    out = await gather_optional(a=_fails(), b=_fails(), c=_ok(3))

    assert set(out["_unavailable"]) == {"a", "b"}
    assert out["c"] == 3


async def test_everything_failing_is_still_a_response():
    out = await gather_optional(a=_fails(), b=_fails())

    assert out["a"] is None
    assert len(out["_unavailable"]) == 2


async def test_an_unexpected_exception_is_contained_too():
    """Not just DownstreamUnavailable — any failure must stay in its panel."""
    async def explodes():
        raise ValueError("bug in a client")

    out = await gather_optional(good=_ok(1), broken=explodes())

    assert out["good"] == 1
    assert out["broken"] is None
    assert out["_unavailable"] == ["broken"]


# ── Error classification ─────────────────────────────────────────────────────


def test_downstream_failure_names_the_service():
    """'prediction-service is down' and 'core-api is broken' send whoever is on
    call to different places, so they must not collapse into one error."""
    exc = DownstreamUnavailable("scoring", "timed out")

    assert exc.service == "scoring"
    assert "scoring" in str(exc)
    assert "timed out" in str(exc)
