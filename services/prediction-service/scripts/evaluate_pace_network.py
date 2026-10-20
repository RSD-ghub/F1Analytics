#!/usr/bin/env python
"""Should the lap simulator run on predicted pace instead of model strengths?

Usage:
    python scripts/evaluate_pace_network.py [--api-url http://localhost:8001] [--first 2019] [--last 2025]

For each season, with nothing from that season or after:

* a linear Plackett-Luce model is fitted on 2014..season-1 — the strengths;
* the pace network (``app/training/pace_network.py``) is fitted on every
  driver-race since 2009 before the season — the predicted pace;
* circuit profiles and retirement clustering are built from races before it.

Every race of the season is then simulated lap by lap both ways and scored on
the published markets, and the per-race differences get a paired bootstrap
interval. The lap engine switches to predicted pace only on an interval that
clears zero — which, measured in October 2026 over 2019-2025, it did not.

Laps before 2018 come from jolpica (``ingestion-service/scripts/
backfill_historical_laps.py``); results from 2008 must be ingested.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from app.services import race_pace  # noqa: E402
from app.services.ingestion_client import IngestionClient  # noqa: E402
from app.services.lap_simulation import LapSimulationParameters, simulate  # noqa: E402
from app.services.model import (  # noqa: E402
    PACE_NETWORK_PATH,
    RaceModel,
    feature_names,
    feature_vector,
)
from app.training import corpus, dataset, lap_fit, lap_profiles, plackett_luce, retirements  # noqa: E402
from app.training.backtest import paired_bootstrap  # noqa: E402
from app.training.pace_network import PaceNetwork  # noqa: E402
from train_model import TRAINING_FROM_SEASON  # noqa: E402
from train_neural import calibration_races  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("evaluate_pace_network")

FIRST_PACE_SEASON = 2009
#: Chosen on 2022-23: pace scale 1.5, race-day spread 0.30 s for the pace arm;
#: the strengths arm runs at the lap engine's own fitted settings.
PACE_SCALE = 1.5
RACE_DAY_SD = 0.30
SECONDS_PER_STRENGTH = 0.986
RUNS = 2000


async def load(client, last):
    results, quali, practice, gaps = [], [], [], {}
    for season in range(FIRST_PACE_SEASON - 1, last + 1):
        results += await client.season_results(season)
        quali += await client.season_qualifying(season)
        practice += await client.season_practice(season)
    for season, round_number in sorted({(r.season, r.round) for r in results}):
        laps = await client.race_laps(season, round_number)
        found = race_pace.race_gaps(laps) if laps else {}
        if found:
            gaps[(season, round_number)] = found
    contexts = await corpus.load_race_contexts(client, results)
    return results, quali, practice, gaps, contexts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:8001")
    parser.add_argument("--first", type=int, default=2019)
    parser.add_argument("--last", type=int, default=2025)
    parser.add_argument("--fit-through", type=int, default=2026,
                        help="the final network written by --write is fitted on every "
                             "race through this season")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--cut", default="",
                        help="season:round — score only that season from this round on, "
                             "with everything before the round known (a mid-season check)")
    args = parser.parse_args()

    results, quali, practice, gaps, contexts = asyncio.run(
        load(IngestionClient(args.api_url), max(args.last, args.fit_through)))
    races = {}
    for season in range(FIRST_PACE_SEASON, max(args.last, args.fit_through) + 1):
        for key, drivers, positions, seed in calibration_races(
                results, [season], quali, practice, with_keys=True):
            races[key] = (drivers, positions, seed)
    circuit_of = {(r.season, r.round): r.circuit for r in results}

    rows = [(key, d, feature_vector(d, with_grid=True), gaps.get(key, {}).get(d.driver))
            for key, (drivers, _, _) in sorted(races.items()) for d in drivers]
    seasons = np.array([key[0] for key, _, _, _ in rows])
    order = np.array([key[0] * 100 + key[1] for key, _, _, _ in rows])
    groups = np.array([key[0] * 100 + key[1] for key, _, _, _ in rows])
    x = np.array([vector for _, _, vector, _ in rows])
    y = np.array([100 * gap if gap is not None else np.nan for _, _, _, gap in rows])
    where = {(key, d.driver): i for i, (key, d, _, _) in enumerate(rows)}

    observations = dataset.build_observations(
        [r for r in results if TRAINING_FROM_SEASON - 1 <= r.season <= args.last],
        target_season=args.last + 1, with_grid=True, quali=quali, practice=practice)
    observations = [o for o in observations if o.season >= TRAINING_FROM_SEASON]
    names = feature_names(with_grid=True)
    means, stds = dataset.standardise(observations, names)

    if args.cut:
        cut_season, cut_round = (int(v) for v in args.cut.split(":"))
        folds = [(cut_season, cut_round)]
    else:
        folds = [(season, 1) for season in range(args.first, args.last + 1)]

    baseline, candidate = {}, {}
    for season, first_round in folds:
        cut = season * 100 + first_round
        model = RaceModel(
            weights=plackett_luce.fit(
                [o for o in observations if o.season * 100 + o.round < cut], names),
            means=means, stds=stds, noise_scale=0.3)
        model.retirement_race_k, model.retirement_team_k, _ = retirements.fit(
            [r for r in results if TRAINING_FROM_SEASON <= r.season
             and r.season * 100 + r.round < cut])
        known = (order < cut) & ~np.isnan(y)
        network = PaceNetwork().fit(x[known], y[known], groups[known])
        this = (seasons == season) & (order >= cut)
        predicted = np.full(len(rows), np.nan)
        predicted[this] = network.predict(x[this], groups[this])
        profiles = lap_profiles.build_profiles(
            [c for c in contexts if c.season * 100 + c.round < cut])
        params = LapSimulationParameters(
            seconds_per_strength=SECONDS_PER_STRENGTH, race_day_sd=RACE_DAY_SD,
            start_shuffle_sd=1.0, overtake_threshold=0.05, orderliness_slope=8.0,
            overall_orderliness=profiles["overall"]["orderliness"])

        def arm(use_pace):
            def sample(key):
                drivers, _, seed = races[key]
                profile = lap_profiles.profile_for(profiles, circuit_of.get(key, ""))
                if use_pace:
                    seconds = np.array([predicted[where[(key, d.driver)]] for d in drivers]) \
                        / 100 * profile["typical_lap"]
                    strengths = -PACE_SCALE * seconds / SECONDS_PER_STRENGTH
                else:
                    strengths = model.strengths(drivers)
                return simulate(drivers, strengths, profile, params, seed=seed, runs=RUNS,
                                retirement_race_k=model.retirement_race_k,
                                retirement_team_k=model.retirement_team_k)
            return sample

        keys = sorted(k for k in races if k[0] == season and k[1] >= first_round)
        scores = defaultdict(list)
        for key in keys:
            drivers, positions, _ = races[key]
            for label, use_pace in (("strengths", False), ("pace", True)):
                score = lap_fit.score(arm(use_pace), [(key, drivers, positions)])["market"]
                # Lower is better; stored negated so the bootstrap reads gain as positive.
                (baseline if not use_pace else candidate)[key] = -score
                scores[label].append(score)
        logger.info("%s (%s races): strengths %.4f   predicted pace %.4f", season, len(keys),
                    np.mean(scores["strengths"]), np.mean(scores["pace"]))

    result = paired_bootstrap(baseline, candidate)
    logger.info("\nper-race market improvement from predicted pace: %+.4f "
                "(95%% CI %+.4f .. %+.4f), better in %.0f%% of %s races",
                result["mean"], result["low"], result["high"],
                100 * result["better_share"], result["races"])
    enabled = result["low"] > 0
    verdict = ("switch to predicted pace" if enabled
               else "keep model strengths — the interval does not clear zero")
    logger.info("verdict: %s", verdict)

    if not args.write or args.cut:
        logger.info("\n(pass --write, without --cut, to save the artifact)")
        return 0
    known = ~np.isnan(y)
    network = PaceNetwork().fit(x[known], y[known], groups[known])
    artifact = {
        "version": "pace-v1",
        "fitted_at": datetime.now(timezone.utc).isoformat(),
        "fitted_through": max(args.last, args.fit_through),
        "driver_races": int(known.sum()),
        "feature_names": feature_names(with_grid=True),
        "pace_scale": PACE_SCALE,
        "race_day_sd": RACE_DAY_SD,
        "enabled": bool(enabled),
        "metrics": {
            "seasons": [args.first, args.last],
            "per_race_gain": result["mean"],
            "ci_low": result["low"],
            "ci_high": result["high"],
            "races": result["races"],
            "better_share": result["better_share"],
            "verdict": verdict,
        },
        "network": network.to_dict(),
    }
    path = os.path.abspath(PACE_NETWORK_PATH)
    with open(path, "w") as handle:
        json.dump(artifact, handle, sort_keys=True, separators=(",", ":"))
    logger.info("wrote %s (fitted on %s driver-races)", path, int(known.sum()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
