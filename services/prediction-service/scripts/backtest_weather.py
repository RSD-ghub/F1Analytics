#!/usr/bin/env python
"""Would race weather improve the forecast? Rolling backtest, with error bars.

Usage (services running, Mongo loaded):
    python scripts/backtest_weather.py [--api-url http://localhost:8001] [--json out.json]

Same protocol as scripts/backtest.py: for each held-out season, every variant
is fitted only on earlier seasons and scored on that season's races (post-
qualifying window, with the grid). Variants are the current feature set alone
and with each weather interaction from ``app/training/weather.py``, then all
three. Each is compared with the current model race by race (paired
bootstrap), over all races and over the wet races alone.

The wetness fed in is the rain that actually fell, so this is the best case.
A feature joins the model only if its interval clears zero here; otherwise
the result is recorded next to ``WITHHELD_FEATURES`` like the others.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Dict, List

import httpx
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from app.services.model import feature_names  # noqa: E402
from app.training import backtest, dataset, weather  # noqa: E402
from backtest import linear_factory  # noqa: E402
from train_model import TRAINING_FROM_SEASON, _load_practice  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backtest_weather")


async def _rows(api: str, dataset_name: str, seasons: List[int]) -> List[Dict]:
    out: List[Dict] = []
    async with httpx.AsyncClient(timeout=120) as client:
        for season in seasons:
            response = await client.get(f"{api}/data/{dataset_name}",
                                        params={"season": season, "limit": 20000})
            response.raise_for_status()
            out.extend(response.json())
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:8001")
    parser.add_argument("--seasons", default="2019,2020,2021,2022,2023,2024,2025")
    parser.add_argument("--from-season", type=int, default=TRAINING_FROM_SEASON)
    parser.add_argument("--decay", type=float, default=0.6)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--json", default=None)
    args = parser.parse_args()
    seasons = [int(s) for s in args.seasons.split(",") if s.strip()]

    from app.services.ingestion_client import IngestionClient
    from app.training import corpus

    client = IngestionClient(args.api_url)
    results, quali = asyncio.run(corpus.load(
        client, args.from_season, max(seasons), require_complete=not args.allow_incomplete))
    practice = asyncio.run(_load_practice(client, args.from_season, max(seasons)))
    lap_seasons = list(range(max(2018, args.from_season), max(seasons) + 1))
    stints = asyncio.run(_rows(args.api_url, "stints", lap_seasons))
    gauge = asyncio.run(_rows(args.api_url, "weather", lap_seasons))

    wetness = weather.race_wetness(stints, gauge)
    wet_races = sorted(k for k, w in wetness.items() if w >= weather.WET_RACE)
    logger.info("wetness known for %s races; %s wet (>= %.0f%% of laps on I/W): %s",
                len(wetness), len(wet_races), 100 * weather.WET_RACE, wet_races)

    usable = [r for r in results if r.season >= args.from_season]
    base = dataset.build_observations(usable, target_season=max(seasons), with_grid=True,
                                      decay=args.decay, quali=quali, practice=practice)
    names = feature_names(with_grid=True)
    records = weather.wet_records(usable, wetness)

    variants = {"current": []}
    variants.update({name: [name] for name in weather.CANDIDATES})
    variants["all three"] = list(weather.CANDIDATES)

    folds_by: Dict[str, List[backtest.FoldResult]] = {}
    for label, extra in variants.items():
        observations = weather.augment(base, names, wetness, records, extra) if extra else base
        folds_by[label] = backtest.rolling_folds(
            observations, list(names) + extra, seasons, {"linear": linear_factory},
            decay=args.decay, from_season=args.from_season)

    reference = backtest.pooled(folds_by["current"], "linear")[0]
    keys = [(f.season, r) for f in folds_by["current"] for r in f.rounds]
    wet_index = [i for i, k in enumerate(keys) if wetness.get(k, 0.0) >= weather.WET_RACE]

    summary = {"wet_races": [list(k) for k in wet_races], "races": len(keys),
               "wet_races_scored": len(wet_index), "variants": {}}
    logger.info("\n%-12s %28s %28s", "variant", "all races vs current", "wet races vs current")
    for label in variants:
        if label == "current":
            continue
        scores = backtest.pooled(folds_by[label], "linear")[0]
        overall = backtest.paired_difference(scores, reference)
        wet = (backtest.paired_difference([scores[i] for i in wet_index],
                                          [reference[i] for i in wet_index])
               if len(wet_index) >= 2 else None)
        verdict = backtest.verdict(overall)
        # The pooled verdict alone hides a loss concentrated in the few wet
        # races, which is exactly where these features act.
        if wet is not None and wet["high"] < 0:
            verdict += "; worse on wet races (interval below zero)"
        elif wet is not None and wet["low"] > 0:
            verdict += "; better on wet races (interval above zero)"
        summary["variants"][label] = {"all": overall, "wet": wet, "verdict": verdict}
        fmt = lambda p: "{:+.3f} [{:+.3f}, {:+.3f}]".format(p["mean"], p["low"], p["high"]) if p else "n/a"
        logger.info("%-12s %28s %28s  %s", label, fmt(overall), fmt(wet), verdict)

    if args.json:
        with open(args.json, "w") as handle:
            json.dump(summary, handle, indent=2)
        logger.info("\nwrote %s", os.path.abspath(args.json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
