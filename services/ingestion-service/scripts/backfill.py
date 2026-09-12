#!/usr/bin/env python
"""Operational backfill driver for ingestion-service.

Runs the service's own ingest pipeline directly rather than over HTTP, because a
multi-decade backfill is a background job measured in hours, not a request. It
is the same ``IngestRunner`` the API uses, so it inherits the completeness
guarantee — which is exactly what the ad-hoc pull scripts this replaces did not.

    python scripts/backfill.py results 2010 2025
    python scripts/backfill.py practice 2018 2025
    python scripts/backfill.py full 2018 2025

``full`` adds laps, stints, pit stops, weather and race control on top of
``results``. It is much slower — lap frames are the bulk of what FastF1 serves —
and only meaningful from 2018, which is the first season with lap data.

Keep it off the current season while a race weekend is live. The running
services own that season, and both this and their scheduled jobs replace rows
per session; pointing them at the same round invites one deleting the other's
work.
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.schemas import IngestDepth  # noqa: E402
from app.services.fastf1_source import FastF1Source  # noqa: E402
from app.services.ingest_runner import IngestRunner  # noqa: E402
from app.services.storage import IngestionStore  # noqa: E402
from f1_common.mongo import create_client, get_database  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
for noisy in ("fastf1", "fastf1.core", "fastf1.req", "fastf1.ergast", "fastf1.api"):
    logging.getLogger(noisy).setLevel(logging.CRITICAL)
logger = logging.getLogger("backfill")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DATABASE = os.getenv("MONGO_DATABASE", "f1_ingestion")
CACHE = os.getenv("FASTF1_CACHE", "fastf1-data/cache")


async def main() -> int:
    mode, first, last = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    client = create_client(MONGO_URI, 5000)
    store = IngestionStore(get_database(client, DATABASE))
    await store.ensure_indexes()
    runner = IngestRunner(
        source=FastF1Source(CACHE, load_telemetry=False),
        store=store, max_attempts=3, backoff_seconds=2.0,
    )

    if mode == "full":
        # Everything results depth covers, plus the lap-level frames. Telemetry
        # stays off (see FastF1Source above): it is a derived per-lap summary,
        # it multiplies the cost several times over, and nothing scores on it.
        summary = await runner.run_backfill(
            first, last, only_gaps=True, depth=IngestDepth.FULL
        )
        logger.info(
            "full %s-%s: expected=%s complete=%s gaps=%s",
            first, last, summary.expected, summary.complete, len(summary.open_gaps),
        )
        if summary.open_gaps:
            logger.warning("open gaps: %s", summary.open_gaps[:20])
    elif mode == "results":
        summary = await runner.run_backfill(
            first, last, only_gaps=True, depth=IngestDepth.RESULTS
        )
        logger.info(
            "results %s-%s: expected=%s complete=%s gaps=%s",
            first, last, summary.expected, summary.complete, len(summary.open_gaps),
        )
        if summary.open_gaps:
            logger.warning("open gaps: %s", summary.open_gaps[:20])
    elif mode == "practice":
        # Practice is not part of the completeness manifest: a sprint weekend
        # genuinely has fewer sessions, so "missing FP3" is not a gap.
        await runner.refresh_manifest(first, last)
        expected = await store.list_expected_sessions(first, last)
        done = 0
        for session in expected:
            # FP2 preferred, FP3 then FP1 as fallbacks. Sprint weekends run a
            # single practice session, so an FP2-only pass silently skipped
            # every sprint — six weekends in 2024 alone.
            # One unusable session must not abort an hour-long backfill; the
            # run is resumable, so a failure here costs one weekend, not all of
            # the remaining ones.
            try:
                rows = await runner.ingest_practice(session)
            except Exception as exc:  # noqa: BLE001
                logger.warning("  %s -> skipped (%s)", session.key, str(exc)[:80])
                continue
            if rows:
                done += 1
            logger.info("  %s -> %s rows", session.key, rows)
        logger.info("practice: %s/%s weekends with pace", done, len(expected))
    else:
        raise SystemExit("mode must be 'results' or 'practice'")

    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
