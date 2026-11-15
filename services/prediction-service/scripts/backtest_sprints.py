#!/usr/bin/env python
"""Measure the sprint forecast on sprints that have already run.

Usage:
    python scripts/backtest_sprints.py [--from 2022] [--to 2025] [--api-url URL]

The sprint window publishes win, podium and points on the argument that a
grid-aware model over a shorter race should do at least as well as it does on
Sunday. This is the measurement that argument owes. Every past sprint is
forecast exactly as the live window would have forecast it — features cut at
the round, the model on disk, the sprint's own grid, eight points places, the
shortened retirement rate — and scored against the sprint's result with the
same Brier skill the track record reports.

A market that comes out at or below zero, or that swings sign between seasons
the way the pre-quali win market did, should come out of
``MARKETS_BY_WINDOW[LockWindow.SPRINT]``.

Needs ingestion-service running with sprints backfilled:

    python services/ingestion-service/scripts/backfill.py sprints 2021 2025

The grid is each sprint's recorded starting grid (``grid_position`` on the
sprint result), which is the sprint qualifying order after any penalty. The
live window forecasts on the classification before penalties, so on the rare
penalised sprint this flatters the backtest slightly.

2021 is excluded by default: its sprint grid came from Friday's Grand Prix
qualifying and only three places scored, so it measures a different format.
"""

import argparse
import asyncio
import logging
import os
import sys
from collections import defaultdict
from typing import Dict, List, Sequence, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.schemas import LockWindow, sprint_points_positions  # noqa: E402
from app.services.features import build_snapshot  # noqa: E402
from app.services.ingestion_client import (  # noqa: E402
    GridSlot,
    IngestionClient,
    RaceResult,
)
from app.services.model import RaceModel  # noqa: E402
from app.services.predictor import SPRINT_DISTANCE_FRACTION, derive_seed  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backtest_sprints")

MARKETS = ("win", "podium", "points")


def grid_from_result(rows: Sequence[RaceResult]) -> List[GridSlot]:
    """The sprint's starting order, from its own result.

    A recorded grid of 0 is a pit-lane start (or no grid recorded); those cars
    start behind every gridded car, in classification order among themselves.
    """
    gridded = sorted(
        (row for row in rows if row.grid_position > 0), key=lambda r: r.grid_position
    )
    pit_lane = sorted(
        (row for row in rows if row.grid_position <= 0), key=lambda r: r.position
    )
    return [
        GridSlot(
            season=row.season, round=row.round, driver=row.driver, team=row.team,
            position=slot, grid_position=slot, grid_source="race_result",
        )
        for slot, row in enumerate(gridded + pit_lane, start=1)
    ]


def _brier(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float:
    return sum(
        (p - (1.0 if o else 0.0)) ** 2 for p, o in zip(probabilities, outcomes)
    ) / len(probabilities)


def score_sprint(
    model: RaceModel,
    history: Sequence[RaceResult],
    sprint: Sequence[RaceResult],
    circuit: str = "",
) -> Dict[str, Tuple[float, float]]:
    """``{market: (model brier, uniform baseline brier)}`` for one sprint."""
    season, round_number = sprint[0].season, sprint[0].round
    places = sprint_points_positions(season)
    snapshot = build_snapshot(
        history, season=season, target_round=round_number,
        window=LockWindow.SPRINT, circuit=circuit, grid=grid_from_result(sprint),
    )
    forecast = {
        row.driver: row
        for row in model.predict(
            snapshot.drivers,
            seed=derive_seed(season, round_number, LockWindow.SPRINT),
            points_positions=places,
            distance_fraction=SPRINT_DISTANCE_FRACTION,
        )
    }
    finish = {row.driver: row for row in sprint}
    drivers = sorted(forecast)
    # How many places make each market true — and so how many drivers the
    # uniform baseline spreads its probability over.
    places_for = {"win": 1, "podium": 3, "points": places}

    scored = {}
    for market in MARKETS:
        probabilities = [getattr(forecast[d], "p_" + market) for d in drivers]
        outcomes = [
            d in finish and finish[d].classified and finish[d].position <= places_for[market]
            for d in drivers
        ]
        baseline = min(places_for[market] / len(drivers), 1.0)
        scored[market] = (
            _brier(probabilities, outcomes),
            _brier([baseline] * len(drivers), outcomes),
        )
    return scored


def backtest(
    model: RaceModel,
    results: Sequence[RaceResult],
    sprints: Sequence[RaceResult],
) -> Dict[int, Dict[str, Tuple[float, int]]]:
    """``{season: {market: (skill, sprints)}}``, plus season 0 for all pooled.

    Skill is pooled within a season — total model Brier against total baseline
    Brier — rather than averaged per sprint, matching how the track record
    aggregates and keeping one chaotic sprint from dominating a short season.
    """
    by_race: Dict[Tuple[int, int], List[RaceResult]] = defaultdict(list)
    for row in sprints:
        by_race[(row.season, row.round)].append(row)
    circuits = {(r.season, r.round): r.circuit for r in results if r.circuit}

    totals: Dict[int, Dict[str, List[float]]] = defaultdict(
        lambda: {m: [0.0, 0.0, 0] for m in MARKETS}
    )
    for (season, round_number), rows in sorted(by_race.items()):
        if not any(row.classified and row.position == 1 for row in rows):
            continue
        history = [r for r in results if r.season in (season - 1, season)]
        scored = score_sprint(
            model, history, rows, circuits.get((season, round_number), "")
        )
        for bucket in (season, 0):
            for market, (fitted, baseline) in scored.items():
                entry = totals[bucket][market]
                entry[0] += fitted
                entry[1] += baseline
                entry[2] += 1

    return {
        season: {
            market: (1.0 - fitted / baseline if baseline else 0.0, count)
            for market, (fitted, baseline, count) in markets.items()
        }
        for season, markets in totals.items()
    }


async def _load(client: IngestionClient, first: int, last: int):
    results: List[RaceResult] = []
    sprints: List[RaceResult] = []
    for season in range(first - 1, last + 1):
        results.extend(await client.season_results(season))
        if season >= first:
            sprints.extend(await client.sprint_results(season))
    return results, sprints


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--from", dest="first", type=int, default=2022)
    parser.add_argument("--to", dest="last", type=int, default=2025)
    parser.add_argument(
        "--api-url",
        default=os.getenv("INGESTION_SERVICE_URL", "http://localhost:8001"),
    )
    args = parser.parse_args()

    model = RaceModel.load()
    results, sprints = asyncio.run(
        _load(IngestionClient(args.api_url), args.first, args.last)
    )
    if not sprints:
        logger.error(
            "no sprint results between %s and %s; run the ingestion backfill "
            "with mode 'sprints' first", args.first, args.last,
        )
        return 1

    measured = backtest(model, results, sprints)
    logger.info("model %s, sprint skill vs uniform baseline (Brier):", model.version)
    logger.info("%-8s %8s %8s %8s %8s", "season", "sprints", *MARKETS)
    for season in sorted(s for s in measured if s) + [0]:
        markets = measured.get(season)
        if not markets:
            continue
        logger.info(
            "%-8s %8d %+7.1f%% %+7.1f%% %+7.1f%%",
            season or "all", markets["win"][1],
            *(100 * markets[m][0] for m in MARKETS),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
