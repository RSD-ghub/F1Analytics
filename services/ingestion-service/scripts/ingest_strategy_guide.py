#!/usr/bin/env python
"""Load a strategist's book into the strategy guide, chunked by chapter.

    python scripts/ingest_strategy_guide.py PATH/TO/book.txt \\
        --title "How to Win a Grand Prix" --author "Bernadette Collins"

The book is copyrighted and is never committed: keep the text file outside the
repository and point this at it. Re-running replaces that book's passages, so
it is safe to run again after a chunker change. Prints a summary of what was
parsed so a bad export (no chapters found, front matter swallowed) is caught
here rather than in Bernie's answers.
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.guide import source as guide  # noqa: E402
from app.guide.store import GuideStore  # noqa: E402
from f1_common import cache  # noqa: E402
from f1_common.mongo import create_client, get_database  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("guide")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Load a book into the strategy guide.")
    parser.add_argument("path", help="the book as plain text (UTF-8)")
    parser.add_argument("--title", default="How to Win a Grand Prix")
    parser.add_argument("--author", default="Bernadette Collins")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse and summarise without writing")
    args = parser.parse_args()

    with open(args.path, encoding="utf-8") as handle:
        text = handle.read()
    try:
        passages = guide.parse_book(text, args.title, args.author)
    except guide.GuideUnreadable as exc:
        logger.error("could not read %s: %s", args.path, exc)
        return 1

    chapters = {}
    for passage in passages:
        key = (passage.chapter, passage.chapter_title)
        chapters[key] = chapters.get(key, 0) + 1
    for (number, title), count in sorted(chapters.items()):
        logger.info("  chapter %2d  %-34s %3d passages", number, title[:34], count)
    logger.info("%d passages from %d chapters", len(passages), len(chapters))
    if args.dry_run:
        return 0

    cache.configure(os.getenv("REDIS_URL", ""))
    client = create_client(os.getenv("MONGO_URI", "mongodb://localhost:27017"), 5000)
    store = GuideStore(get_database(client, os.getenv("MONGO_DATABASE", "f1_ingestion")))
    await store.ensure_indexes()
    saved = await store.save_book(passages)
    logger.info("stored %d passages for %r", saved, args.title)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
