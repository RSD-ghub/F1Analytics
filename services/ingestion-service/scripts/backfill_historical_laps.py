#!/usr/bin/env python
"""Backfill race laps before 2018 from jolpica (see app/services/historical_laps.py).

    python scripts/backfill_historical_laps.py 2008 2017
    python scripts/backfill_historical_laps.py 2010 2010 --rounds 1,2
    python scripts/backfill_historical_laps.py 2008 2017 --interval 7.5

Resumable: races that already have laps are skipped. Paced for jolpica's hourly
limit, so a decade takes a few hours; run it in the background.
"""

import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import get_settings  # noqa: E402
from app.services import historical_laps  # noqa: E402
from app.services.storage import IngestionStore  # noqa: E402
from f1_common.mongo import create_client, get_database  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("backfill_historical_laps")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DATABASE = os.getenv("MONGO_DATABASE", "f1_ingestion")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("first", type=int)
    parser.add_argument("last", type=int)
    parser.add_argument("--rounds", default="")
    parser.add_argument("--interval", type=float, default=7.5,
                        help="seconds between jolpica requests")
    args = parser.parse_args()

    client = create_client(MONGO_URI)
    store = IngestionStore(get_database(client, DATABASE))
    rounds = None
    if args.rounds:
        rounds = {s: [int(r) for r in args.rounds.split(",")]
                  for s in range(args.first, args.last + 1)}
    jolpica = historical_laps.JolpicaClient(get_settings().ergast_base_url, args.interval)
    summary = await historical_laps.backfill(store, jolpica, args.first, args.last, rounds)
    logger.info("done: %s (%s requests)", json.dumps(summary), jolpica.requests)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
