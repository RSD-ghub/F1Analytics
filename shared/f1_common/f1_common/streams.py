"""Redis Streams for the live race, optional like the cache.

Two streams carry a race while it runs:

* ``f1:live:laps`` — ingestion adds an entry each time the live feed's
  leader completes a lap or the session status changes. The prediction
  service's tracker blocks on it, so it forecasts a lap the moment it exists
  instead of on its next poll.
* ``f1:live:frames:{season}-{round}`` — the tracker adds each lap's forecast.
  core-api's event stream blocks on it and sends frames straight to the
  browser, and a restarted tracker reads it back, so a service restart during
  the race keeps the laps already forecast.

Entries are one field, ``data``, holding JSON. Streams are capped
(``MAXLEN ~``) so a season of races cannot grow without bound.

**Never load-bearing.** With no ``REDIS_URL`` everything here is a no-op and
the services fall back to polling each other over HTTP, which is how tests
and a Mac without Redis run. A publish that fails is logged and dropped; a
read that fails raises ``StreamsUnavailable`` so the caller falls back to
polling for that turn.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

LAPS = "f1:live:laps"
#: Entries kept per stream: a race is ~70 laps; a few races of laps.
MAXLEN = 2000

_client: Any = None


class StreamsUnavailable(RuntimeError):
    pass


def frames_stream(season: int, round_number: int) -> str:
    return "f1:live:frames:{}-{:02d}".format(int(season), int(round_number))


def configure(url: str, timeout_seconds: float = 30.0) -> bool:
    """Connect lazily; empty ``url`` leaves streams off. The socket timeout
    is longer than any block a reader asks for."""
    global _client
    if not url:
        _client = None
        return False
    import redis.asyncio as redis_asyncio

    _client = redis_asyncio.Redis.from_url(
        url, socket_timeout=timeout_seconds, socket_connect_timeout=2.0)
    return True


def use_client(client: Any) -> None:
    """Install a ready-made client (tests); None turns streams off."""
    global _client
    _client = client


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


def _decode(entries) -> List[Tuple[str, Dict[str, Any]]]:
    out = []
    for entry_id, fields in entries or []:
        entry_id = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
        raw = fields.get(b"data", fields.get("data"))
        if isinstance(raw, bytes):
            raw = raw.decode()
        try:
            out.append((entry_id, json.loads(raw)))
        except (TypeError, ValueError):
            continue
    return out


async def publish(stream: str, data: Dict[str, Any]) -> Optional[str]:
    """Add one entry; its id, or None when streams are off or Redis failed."""
    if _client is None:
        return None
    try:
        entry = await _client.xadd(stream, {"data": json.dumps(data, default=str)},
                                   maxlen=MAXLEN, approximate=True)
    except Exception as exc:
        logger.warning("redis stream %s: publish failed: %s", stream, exc)
        return None
    return entry.decode() if isinstance(entry, bytes) else str(entry)


async def read_after(stream: str, after: str = "$", block_ms: int = 0,
                     count: int = 200) -> List[Tuple[str, Dict[str, Any]]]:
    """Entries after ``after`` ("$" = only new ones), waiting up to
    ``block_ms`` for the first. Raises StreamsUnavailable."""
    if _client is None:
        raise StreamsUnavailable("streams are off")
    try:
        answer = await _client.xread({stream: after}, count=count,
                                     block=block_ms if block_ms > 0 else None)
    except Exception as exc:
        raise StreamsUnavailable(str(exc)) from exc
    entries = []
    for _, rows in answer or []:
        entries.extend(rows)
    return _decode(entries)


async def history(stream: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Every entry still in ``stream``, oldest first. Raises StreamsUnavailable."""
    if _client is None:
        raise StreamsUnavailable("streams are off")
    try:
        return _decode(await _client.xrange(stream))
    except Exception as exc:
        raise StreamsUnavailable(str(exc)) from exc


async def last_id(stream: str) -> str:
    """The newest entry's id, or "0-0" for an empty stream. Raises StreamsUnavailable."""
    if _client is None:
        raise StreamsUnavailable("streams are off")
    try:
        rows = await _client.xrevrange(stream, count=1)
    except Exception as exc:
        raise StreamsUnavailable(str(exc)) from exc
    decoded = _decode(rows)
    return decoded[0][0] if decoded else "0-0"


class MemoryStreams:
    """The few stream commands used here, in memory, for tests. Reads never
    block; "$" returns nothing, as it would with no new entry."""

    def __init__(self):
        self.streams: Dict[str, List[Tuple[str, Dict[str, bytes]]]] = {}
        self.down = False
        self._seq = 0

    def _check(self):
        if self.down:
            raise ConnectionError("redis is down")

    async def xadd(self, stream, fields, maxlen=None, approximate=True):
        self._check()
        self._seq += 1
        entry = "{}-0".format(self._seq)
        rows = self.streams.setdefault(stream, [])
        rows.append((entry, {k: v.encode() for k, v in fields.items()}))
        if maxlen:
            del rows[:-maxlen]
        return entry.encode()

    async def xread(self, streams, count=None, block=None):
        self._check()
        out = []
        for stream, after in streams.items():
            if after == "$":
                continue
            seq = int(str(after).split("-")[0])
            new = [r for r in self.streams.get(stream, []) if int(r[0].split("-")[0]) > seq]
            if new:
                out.append((stream.encode(), [(e.encode(), f) for e, f in new[:count]]))
        return out

    async def xrange(self, stream):
        self._check()
        return [(e.encode(), f) for e, f in self.streams.get(stream, [])]

    async def xrevrange(self, stream, count=None):
        self._check()
        rows = list(reversed(self.streams.get(stream, [])))
        return [(e.encode(), f) for e, f in rows[:count]]

    async def aclose(self):
        pass
