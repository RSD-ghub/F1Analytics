#!/usr/bin/env python
"""Fetch and store the FIA regulations, chunked by article.

    python scripts/ingest_regulations.py 2026

Operational, like backfill.py: a handful of large PDFs, run when the FIA
reissues a section rather than on a timer. Sections are reissued often — Section
B ran to eight issues during 2026 — so re-running is cheap and expected.
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import regulations  # noqa: E402
from app.services.storage import IngestionStore  # noqa: E402
from f1_common.mongo import create_client, get_database  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("regulations")


async def main() -> int:
    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    sections = (
        tuple(s.upper() for s in sys.argv[2].split(","))
        if len(sys.argv) > 2 else regulations.DEFAULT_SECTIONS
    )

    client = create_client(os.getenv("MONGO_URI", "mongodb://localhost:27017"), 5000)
    store = IngestionStore(get_database(client, os.getenv("MONGO_DATABASE", "f1_ingestion")))
    await store.ensure_indexes()

    documents = [d for d in await regulations.discover(season) if d.section in sections]
    if not documents:
        logger.error("no sections matched %s for %s", sections, season)
        return 1

    total = 0
    for document in documents:
        try:
            articles = await regulations.fetch_articles(document)
        except regulations.RegulationsUnavailable as exc:
            logger.warning("  %s -> skipped (%s)", document.label, str(exc)[:90])
            continue
        saved = await store.save_regulations(articles)
        total += saved
        logger.info("  %-46s %4d articles", document.label[:46], saved)

    logger.info("\nstored %d articles for %s", total, season)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
