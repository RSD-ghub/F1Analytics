"""A shared Redis cache that is never load-bearing.

Two halves, used by different services:

**Readers** (core-api) wrap a fetch in :func:`read_through`. The answer is kept
under a key that embeds the current *generation* of every data domain it was
built from.

**Writers** (ingestion, prediction, scoring) call :func:`bump` — usually via the
:func:`invalidates` decorator on a storage method — after they change a domain.
Bumping moves the generation on, so every key built from the old one stops
being asked for and ages out on its TTL. Nothing has to know which keys exist,
and a reader that fetched just before a write stores its answer under the old
generation, where nobody will read it: the ordering race that per-key deletes
have is not possible here.

Redis being down, slow or absent changes speed and nothing else. Every call
fails open to the uncached path, and after a failure Redis is left alone for a
short cooldown so an outage does not add a timeout to every request. With no
``REDIS_URL`` configured the whole module is a no-op, which is how tests and
the bare-uvicorn dev setup run.

Generation keys carry no TTL, and the server is run with
``maxmemory-policy volatile-lru`` so only TTL'd entries are ever evicted. An
evicted generation would restart at zero and could resurrect entries written
under an earlier zero.
"""

import functools
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

KEY_PREFIX = "f1:cache:v1"

#: How long to stop talking to Redis after it fails.
COOLDOWN_SECONDS = 30.0

_client: Any = None
_suspended_until = 0.0


def configure(url: str, timeout_seconds: float = 0.25) -> bool:
    """Connect lazily to ``url``; an empty URL leaves caching off.

    Returns whether caching is on. Does no I/O, so a Redis that is down at boot
    does not stop a service starting.
    """
    global _client, _suspended_until
    _suspended_until = 0.0
    if not url:
        _client = None
        return False
    import redis.asyncio as redis_asyncio

    _client = redis_asyncio.Redis.from_url(
        url,
        socket_timeout=timeout_seconds,
        socket_connect_timeout=timeout_seconds,
    )
    return True


def use_client(client: Any) -> None:
    """Install a ready-made client (tests)."""
    global _client, _suspended_until
    _client = client
    _suspended_until = 0.0


async def close() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
    _client = None


def enabled() -> bool:
    return _client is not None


def _usable() -> Any:
    if _client is None or time.monotonic() < _suspended_until:
        return None
    return _client


def _trip(exc: Exception) -> None:
    global _suspended_until
    if time.monotonic() >= _suspended_until:
        logger.warning(
            "redis unavailable, serving uncached for %ss: %s", COOLDOWN_SECONDS, exc
        )
    _suspended_until = time.monotonic() + COOLDOWN_SECONDS


def generation_key(domain: str) -> str:
    return "{}:gen:{}".format(KEY_PREFIX, domain)


async def bump(*domains: str) -> None:
    """Mark every cached answer built from ``domains`` as out of date. Never raises."""
    client = _usable()
    if client is None or not domains:
        return
    try:
        pipe = client.pipeline(transaction=False)
        for domain in domains:
            pipe.incr(generation_key(domain))
        await pipe.execute()
    except Exception as exc:
        # A missed bump is bounded by the entry's TTL, which is why every
        # cached path has one.
        _trip(exc)


def invalidates(*domains: str) -> Callable:
    """Decorate an async write so that it bumps ``domains`` once it succeeds."""

    def wrap(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def inner(*args: Any, **kwargs: Any) -> Any:
            result = await fn(*args, **kwargs)
            await bump(*domains)
            return result

        return inner

    return wrap


def _entry_key(
    namespace: str, ident: str, domains: Sequence[str], generations: Iterable[Any]
) -> str:
    stamp = ",".join(
        "{}={}".format(domain, int(gen or 0)) for domain, gen in zip(domains, generations)
    )
    return "{}:entry:{}:{}:{}".format(KEY_PREFIX, namespace, stamp, ident)


async def read_through(
    namespace: str,
    ident: str,
    ttl_seconds: int,
    domains: Sequence[str],
    load: Callable[[], Awaitable[Any]],
) -> Any:
    """Return the cached answer for ``ident``, or ``await load()`` and keep it.

    ``load`` must return something JSON-serialisable. Exceptions from ``load``
    propagate and are never cached: a downstream outage is not an answer.
    """
    client = _usable()
    if client is None:
        return await load()

    try:
        generations = await client.mget([generation_key(d) for d in domains])
        key = _entry_key(namespace, ident, domains, generations)
        raw = await client.get(key)
    except Exception as exc:
        _trip(exc)
        return await load()

    if raw is not None:
        try:
            return json.loads(raw)
        except ValueError:
            pass

    value = await load()
    try:
        await client.set(key, json.dumps(value), ex=ttl_seconds)
    except Exception as exc:
        _trip(exc)
    return value


def query_ident(path: str, params: Optional[Dict[str, Any]] = None) -> str:
    """A stable identity for a GET: the path plus its parameters, sorted."""
    if not params:
        return path
    pairs = sorted((str(k), "" if v is None else str(v)) for k, v in params.items())
    return "{}?{}".format(path, "&".join("{}={}".format(k, v) for k, v in pairs))
