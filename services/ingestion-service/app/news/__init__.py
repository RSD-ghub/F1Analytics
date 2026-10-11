"""Paddock news: reading the outlets' RSS feeds (``source``), storing and
searching the headlines (``store``), and the read API over them (``router``).

Shaped like ``app.regulations`` on purpose — one source module, one store, one
router — and for the same reason: a different kind of thing from race sessions,
sharing this process for its Mongo client and nothing else.
"""

from app.news.store import NewsStore

__all__ = ["NewsStore"]
