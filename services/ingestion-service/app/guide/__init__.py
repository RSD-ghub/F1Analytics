"""The strategy guide: a strategist's book chunked into citable passages
(``source``), ranked against questions (``retrieval``), stored (``store``), and
the read API over them (``router``).

Shaped like ``app.regulations`` and ``app.news``. The book's text is never in
the repository; it is loaded into the database from a file the operator holds.
"""

from app.guide.store import GuideStore

__all__ = ["GuideStore"]
