"""The Redis read-through in front of the internal services.

What is protected: a write anywhere retires what core-api remembers about it,
nothing that failed is remembered, only the paths that opt in are cached, and
Redis being down makes the site slower rather than broken.
"""

import pytest

from app.services import cache_policy
from app.services.downstream import DownstreamUnavailable, ServiceClient
from f1_common import cache


class FakeRedis:
    """The handful of commands the cache uses, in memory."""

    def __init__(self):
        self.data = {}
        self.ttls = {}
        self.down = False

    def _check(self):
        if self.down:
            raise ConnectionError("redis is down")

    async def mget(self, keys):
        self._check()
        return [self.data.get(k) for k in keys]

    async def get(self, key):
        self._check()
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self._check()
        self.data[key] = value.encode() if isinstance(value, str) else value
        self.ttls[key] = ex

    def pipeline(self, transaction=True):
        return _Pipeline(self)

    async def aclose(self):
        pass


class _Pipeline:
    def __init__(self, redis):
        self._redis = redis
        self._keys = []

    def incr(self, key):
        self._keys.append(key)

    async def execute(self):
        self._redis._check()
        for key in self._keys:
            self._redis.data[key] = int(self._redis.data.get(key) or 0) + 1


@pytest.fixture
def redis():
    fake = FakeRedis()
    cache.use_client(fake)
    yield fake
    cache.use_client(None)


@pytest.fixture
def calls(monkeypatch):
    """Count what actually reaches the downstream service."""
    seen = []

    async def fetch(self, path, params=None):
        seen.append((self.name, path, params))
        if path == "/broken":
            raise DownstreamUnavailable(self.name, "boom")
        return {"path": path, "n": len(seen)}

    monkeypatch.setattr(ServiceClient, "_fetch", fetch)
    return seen


def _ingestion():
    return ServiceClient("ingestion", "http://ingestion:8001")


async def test_a_second_read_is_answered_from_redis(redis, calls):
    first = await _ingestion().get("/forward/calendar/2026")
    second = await _ingestion().get("/forward/calendar/2026")
    assert first == second
    assert len(calls) == 1


async def test_a_write_retires_the_cached_answer(redis, calls):
    await _ingestion().get("/circuits/2026/18")
    await cache.bump("ingestion")
    fresh = await _ingestion().get("/circuits/2026/18")
    assert len(calls) == 2
    assert fresh["n"] == 2


async def test_a_write_elsewhere_leaves_it_alone(redis, calls):
    await _ingestion().get("/circuits/2026/18")
    await cache.bump("scoring")
    await _ingestion().get("/circuits/2026/18")
    assert len(calls) == 1


async def test_an_answer_built_from_two_domains_follows_both(redis, calls):
    prediction = ServiceClient("prediction", "http://prediction:8002")
    await prediction.get("/championship/2026")
    await cache.bump("ingestion")
    await prediction.get("/championship/2026")
    assert len(calls) == 2


async def test_parameters_are_part_of_the_key(redis, calls):
    client = _ingestion()
    await client.get("/data/results", {"season": 2026, "round": 1})
    await client.get("/data/results", {"round": 1, "season": 2026})
    await client.get("/data/results", {"season": 2026, "round": 2})
    assert len(calls) == 2


async def test_a_failure_is_never_remembered(redis, calls, monkeypatch):
    monkeypatch.setattr(
        cache_policy, "lookup", lambda service, path: cache_policy.Policy(60, ("x",))
    )
    for _ in range(2):
        with pytest.raises(DownstreamUnavailable):
            await _ingestion().get("/broken")
    assert len(calls) == 2
    assert not [k for k in redis.data if "broken" in k]


async def test_paths_that_do_not_opt_in_are_always_live(redis, calls):
    for _ in range(2):
        await _ingestion().get("/ingest/status")
    assert len(calls) == 2
    assert cache_policy.lookup("ingestion", "/health") is None
    assert cache_policy.lookup("ingestion", "/ingest/status") is None


async def test_redis_down_means_uncached_not_broken(redis, calls):
    redis.down = True
    first = await _ingestion().get("/forward/calendar/2026")
    second = await _ingestion().get("/forward/calendar/2026")
    assert first["path"] == second["path"] == "/forward/calendar/2026"
    assert len(calls) == 2
    await cache.bump("ingestion")  # does not raise


async def test_no_redis_configured_is_a_plain_fetch(calls):
    cache.use_client(None)
    await _ingestion().get("/forward/calendar/2026")
    await _ingestion().get("/forward/calendar/2026")
    assert len(calls) == 2


async def test_a_null_answer_is_cached_too(redis, monkeypatch):
    seen = []

    async def fetch(self, path, params=None):
        seen.append(path)
        return None

    monkeypatch.setattr(ServiceClient, "_fetch", fetch)
    assert await _ingestion().get("/forward/next") is None
    assert await _ingestion().get("/forward/next") is None
    assert len(seen) == 1


async def test_the_decorator_bumps_only_after_the_write_succeeds(redis):
    @cache.invalidates("scoring")
    async def write(fail):
        if fail:
            raise RuntimeError("write failed")

    with pytest.raises(RuntimeError):
        await write(True)
    assert redis.data.get(cache.generation_key("scoring")) is None
    await write(False)
    assert redis.data[cache.generation_key("scoring")] == 1


def test_every_cached_path_has_a_ttl():
    for service, rules in cache_policy._POLICIES.items():
        for pattern, policy in rules:
            assert policy.ttl_seconds > 0, (service, pattern)
            assert policy.domains, (service, pattern)


def test_the_clock_driven_paths_stay_short():
    assert cache_policy.lookup("ingestion", "/forward/next").ttl_seconds <= 60
    freshness = cache_policy.lookup("ingestion", "/forward/qualifying-freshness/2026/18")
    assert freshness.ttl_seconds <= 60
