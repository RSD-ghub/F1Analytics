#!/usr/bin/env python
"""Operational backfill driver for ingestion-service.

Runs the service's own ingest pipeline directly rather than over HTTP, because a
multi-decade backfill is a background job measured in hours, not a request. It
is the same ``IngestRunner`` the API uses, so it inherits the completeness
guarantee — which is exactly what the ad-hoc pull scripts this replaces did not.

    python scripts/backfill.py results 2010 2025
    python scripts/backfill.py practice 2018 2025
    python scripts/backfill.py full 2018 2025
    python scripts/backfill.py full 2018 2025 --refresh   # re-read, after a schema change
    python scripts/backfill.py sprints 2021 2025
    python scripts/backfill.py full 2026 2026 --round 19   # re-read one race
    python scripts/backfill.py positions 2018 2026          # add lap positions only
    python scripts/backfill.py positions 2026 2026 --round 2

``positions`` reads each race again for its per-lap positions and sets them on
the laps already stored, and does nothing else: no row is inserted, replaced or
deleted, so it cannot lose a lap the way a ``--refresh`` can when the source
now returns fewer. Races whose stored laps all have a position are skipped
unless ``--all`` is given. Where official timing has no positions (FastF1
cannot load Monza 2018), jolpica's lap chart is used instead.

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
from app.services.openf1_source import OpenF1Client  # noqa: E402
from app.services.ingest_runner import IngestRunner  # noqa: E402
from app.services.storage import IngestionStore  # noqa: E402
from f1_common import cache  # noqa: E402
from f1_common.mongo import create_client, get_database  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
for noisy in ("fastf1", "fastf1.core", "fastf1.req", "fastf1.ergast", "fastf1.api"):
    logging.getLogger(noisy).setLevel(logging.CRITICAL)
logger = logging.getLogger("backfill")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DATABASE = os.getenv("MONGO_DATABASE", "f1_ingestion")
CACHE = os.getenv("FASTF1_CACHE", "fastf1-data/cache")
OPENF1 = os.getenv("OPENF1_BASE_URL", "https://api.openf1.org/v1")


def _round_arg(args):
    """The value after ``--round``, or None."""
    if "--round" not in args:
        return None
    at = args.index("--round")
    try:
        return int(args[at + 1])
    except (IndexError, ValueError):
        raise SystemExit("--round needs a round number")


async def main() -> int:
    mode, first, last = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])

    # ``--refresh`` re-reads sessions already marked complete.
    #
    # Normally the opposite is wanted: a backfill heals gaps and skips what it
    # already has, which is what makes a scheduled re-run cheap. But a schema
    # change makes "complete" mean something new, and every stored session is
    # then complete at the old shape and stale at the new one, with no gap to
    # heal. Capturing speed traps and sector times was exactly that case.
    #
    # Cheap when the FastF1 cache is warm, since nothing then goes to the
    # network; set BACKFILL_SESSIONS_PER_HOUR=0 to drop the quota pacing that
    # only exists to protect API calls that are not being made.
    refresh = "--refresh" in sys.argv[4:]
    # ``--round N`` re-reads that one race of ``first`` and nothing else,
    # always as a refresh. A full-season refresh to correct one round rewrites
    # every other round with it.
    only_round = _round_arg(sys.argv[4:])
    # Writes from here retire what core-api has cached, as the service's own
    # would. Without REDIS_URL they are not announced, and cached answers
    # catch up when their TTLs run out.
    cache.configure(os.getenv("REDIS_URL", ""))
    client = create_client(MONGO_URI, 5000)
    store = IngestionStore(get_database(client, DATABASE))
    await store.ensure_indexes()
    runner = IngestRunner(
        # OpenF1 fills a race official timing has no laps for, and finishes
        # one whose official laps stop early (a red-flag restart).
        source=FastF1Source(CACHE, load_telemetry=False, openf1=OpenF1Client(OPENF1)),
        store=store, max_attempts=3, backoff_seconds=2.0,
        # Paced: an unthrottled full-depth run burns FastF1's hourly quota in
        # minutes and then stops, turning one pass into four across four hours.
        sessions_per_hour=int(os.getenv("BACKFILL_SESSIONS_PER_HOUR", "55")),
    )

    if mode == "positions":
        from app.config import get_settings
        from app.services.historical_laps import JolpicaClient

        jolpica = JolpicaClient(get_settings().ergast_base_url)
        every = "--all" in sys.argv[4:]
        for season in range(first, (first if only_round is not None else last) + 1):
            await runner.refresh_manifest(season, season)
            for expected in await store.list_expected_sessions(season, season):
                if only_round is not None and expected.round != only_round:
                    continue
                where = {"season": season, "round": expected.round}
                if not await store.count_matching("laps", where):
                    continue
                missing = await store.count_matching(
                    "laps", dict(where, position={"$not": {"$gt": 0}}))
                if not missing and not every:
                    continue
                outcome = await runner.fill_lap_positions(expected, jolpica=jolpica)
                after = await store.count_matching(
                    "laps", dict(where, position={"$not": {"$gt": 0}}))
                logger.info("%s: %s -> laps without a position %s -> %s",
                            expected.key, outcome, missing, after)
        return 0

    if only_round is not None:
        if mode != "full":
            raise SystemExit("--round works with 'full' only")
        await runner.refresh_manifest(first, first)
        expected = [
            session for session in await store.list_expected_sessions(first, first)
            if session.round == only_round
        ]
        if not expected:
            raise SystemExit("no round {} in {}".format(only_round, first))
        state = await runner.ingest_session(expected[0], depth=IngestDepth.FULL)
        logger.info("%s -> %s", expected[0].key, state.state.value)
        for check in getattr(state.integrity, "checks", None) or []:
            if not check.passed:
                logger.warning("  %s: %s", check.name, check.detail)
    elif mode == "full":
        # Everything results depth covers, plus the lap-level frames. Telemetry
        # stays off (see FastF1Source above): it is a derived per-lap summary,
        # it multiplies the cost several times over, and nothing scores on it.
        summary = await runner.run_backfill(
            first, last, only_gaps=not refresh, depth=IngestDepth.FULL
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
    elif mode == "sprints":
        # Sprint qualifying and sprint results, for scoring and for the sprint
        # backtest. The timetable first: sprint sessions are found by the
        # names it publishes, and a season never refreshed has none.
        await runner.refresh_weekends(first, last)
        for season in range(first, last + 1):
            outcome = await runner.ingest_sprint_sessions(
                season, include_finished=True
            )
            logger.info(
                "sprints %s: ingested=%s waiting=%s failed=%s",
                season, len(outcome["ingested"]), len(outcome["waiting"]),
                len(outcome["failed"]),
            )
            for label in outcome["failed"]:
                logger.warning("  failed: %s", label)
    else:
        raise SystemExit("mode must be 'results', 'practice', 'full' or 'sprints'")

    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
