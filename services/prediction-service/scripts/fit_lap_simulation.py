#!/usr/bin/env python
"""Fit the lap-by-lap race simulator, race it against the sampler, write the artifact.

Usage:
    python scripts/fit_lap_simulation.py [--api-url http://localhost:8001] [--write]

Same splits as the race models:

* **train** (2018-2021, the seasons with lap timing before validation) — the
  pace scale is fitted here, on how the race model's strength predicted each
  driver's measured race pace. A linear Plackett-Luce model is fitted on
  2014-2021 for this, so nothing about it has seen a later race.
* **validation** (2022-2023) — race-day spread and the passing threshold are
  chosen here, on the published-market score.
* **test** (2024-2025) — scored once, the simulator against the Plackett-Luce
  sampler on the same races, same strengths, same retirement clustering.

Circuit profiles used for validation and test are built only from races before
the validation seasons. The profiles written to the artifact are rebuilt from
every race, for serving.

**The gate.** ``enabled`` is written true only if the simulator scores no worse
than the sampler on the test seasons' published markets. The race simulation
uses the lap engine only when it is enabled; otherwise it keeps sampling.
"""

import argparse
import asyncio
import itertools
import json
import logging
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from app.services.ingestion_client import IngestionClient  # noqa: E402
from app.services.lap_simulation import (  # noqa: E402
    LAP_ENGINE_VERSION,
    LapSimulationParameters,
    simulate,
)
from app.services.model import LAP_SIMULATION_PATH, RaceModel, feature_names  # noqa: E402
from app.training import corpus, dataset, lap_fit, lap_profiles, plackett_luce, retirements  # noqa: E402
from train_model import TRAINING_FROM_SEASON, _load_practice  # noqa: E402
from train_neural import calibration_races  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("fit_lap_simulation")

RACE_DAY_CANDIDATES = (0.25, 0.30, 0.36)
THRESHOLD_CANDIDATES = (0.05, 0.10, 0.20)
ORDERLINESS_SLOPE = 8.0
VALIDATION_RUNS = 1500
TEST_RUNS = 4000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:8001")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--test-seasons", default="2024,2025")
    parser.add_argument("--validation-seasons", default="2022,2023")
    parser.add_argument("--output", default=LAP_SIMULATION_PATH)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    test_seasons = [int(s) for s in args.test_seasons.split(",")]
    validation_seasons = [int(s) for s in args.validation_seasons.split(",")]
    last = max(test_seasons)
    first_held_out = min(validation_seasons + test_seasons)

    client = IngestionClient(args.api_url)
    results, quali = asyncio.run(corpus.load(
        client, TRAINING_FROM_SEASON, last, require_complete=not args.allow_incomplete))
    practice = asyncio.run(_load_practice(client, TRAINING_FROM_SEASON, last))
    contexts = asyncio.run(corpus.load_race_contexts(client, results))
    by_key = {(c.season, c.round): c for c in contexts}

    # A strength model that has seen nothing from the validation seasons on.
    observations = dataset.build_observations(
        results, target_season=last + 1, with_grid=True, quali=quali, practice=practice)
    names = feature_names(with_grid=True)
    means, stds = dataset.standardise(observations, names)
    train = [o for o in observations if o.season < first_held_out]
    model = RaceModel(weights=plackett_luce.fit(train, names), means=means, stds=stds,
                      noise_scale=0.3)
    model.retirement_race_k, model.retirement_team_k, _ = retirements.fit(
        [r for r in results if r.season < first_held_out])

    def races(seasons):
        return calibration_races(results, seasons, quali, practice, with_keys=True)

    fitting = races(sorted({c.season for c in contexts if c.season < first_held_out}))
    pairs = []
    for key, drivers, _, _ in fitting:
        fit = by_key[key].fit if key in by_key else None
        if fit is None:
            continue
        strengths = model.strengths(drivers)
        both = [(strengths[i], fit.pace[d.driver])
                for i, d in enumerate(drivers) if d.driver in fit.pace]
        if len(both) >= 8:
            array = np.array(both)
            pairs.append((array[:, 0], array[:, 1]))
    seconds_per_strength, race_day_sd, points = lap_fit.fit_pace_scale(pairs)
    logger.info("\npace scale on %s races (%s drivers): %.3f s per strength unit, "
                "race-day spread %.3f s", len(pairs), points, seconds_per_strength, race_day_sd)

    early = lap_profiles.build_profiles([c for c in contexts if c.season < first_held_out])
    validation = races(validation_seasons)
    test = races(test_seasons)
    seeds = {key: seed for key, _, _, seed in validation + test}
    circuit_of = {(r.season, r.round): r.circuit for r in results}

    def lap_sampler(params, profiles, runs):
        def sample(key):
            drivers = next(d for k, d, _, _ in validation + test if k == key)
            return simulate(
                drivers, model.strengths(drivers),
                lap_profiles.profile_for(profiles, circuit_of.get(key, "")), params,
                seed=seeds[key], runs=runs,
                retirement_race_k=model.retirement_race_k,
                retirement_team_k=model.retirement_team_k)
        return sample

    def plackett_luce_sampler(runs):
        def sample(key):
            drivers = next(d for k, d, _, _ in validation + test if k == key)
            return model.sample_outcomes(drivers, seed=seeds[key], runs=runs)
        return sample

    def scored(sample, split):
        return lap_fit.score(sample, [(k, d, a) for k, d, a, _ in split])

    logger.info("\nsearch on validation %s (%s races):", validation_seasons, len(validation))
    sweep = []
    for spread, threshold in itertools.product(
            sorted(set(RACE_DAY_CANDIDATES + (round(race_day_sd, 2),))), THRESHOLD_CANDIDATES):
        params = LapSimulationParameters(
            seconds_per_strength=seconds_per_strength, race_day_sd=spread,
            start_shuffle_sd=1.0, overtake_threshold=threshold,
            orderliness_slope=ORDERLINESS_SLOPE,
            overall_orderliness=early["overall"]["orderliness"])
        result = scored(lap_sampler(params, early, VALIDATION_RUNS), validation)
        sweep.append(dict(race_day_sd=spread, overtake_threshold=threshold, **result))
        logger.info("  race-day %.2f  threshold %.2f   market %.4f  positions %.4f  "
                    "orderliness %.3f (real %.3f)", spread, threshold, result["market"],
                    result["positions"], result["orderliness_simulated"],
                    result["orderliness_observed"])
    best = min(sweep, key=lambda row: row["market"])
    params = LapSimulationParameters(
        seconds_per_strength=seconds_per_strength, race_day_sd=best["race_day_sd"],
        start_shuffle_sd=1.0, overtake_threshold=best["overtake_threshold"],
        orderliness_slope=ORDERLINESS_SLOPE,
        overall_orderliness=early["overall"]["orderliness"])
    logger.info("chosen: race-day %.2f, threshold %.2f",
                params.race_day_sd, params.overtake_threshold)

    lap_test = scored(lap_sampler(params, early, TEST_RUNS), test)
    sampler_test = scored(plackett_luce_sampler(TEST_RUNS), test)
    enabled = lap_test["market"] <= sampler_test["market"]
    logger.info("\n=== held-out %s (%s races) ===", test_seasons, len(test))
    logger.info("%-30s %12s %12s", "", "sampler", "lap-by-lap")
    for label, key in (("published markets (Brier)", "market"),
                       ("finishing positions (RPS)", "positions"),
                       ("orderliness, simulated", "orderliness_simulated"),
                       ("orderliness percentile", "orderliness_percentile")):
        logger.info("%-30s %12.4f %12.4f", label, sampler_test[key], lap_test[key])
    logger.info("%-30s %12.4f", "orderliness, real races", lap_test["orderliness_observed"])
    logger.info("\nverdict: %s", "ENABLE — no worse on the published markets" if enabled
                else "keep sampling — worse on the published markets")

    if not args.write:
        logger.info("\n(dry run — pass --write to save the artifact)")
        return 0

    profiles = lap_profiles.build_profiles(contexts)
    params.overall_orderliness = profiles["overall"]["orderliness"]
    artifact = {
        "version": LAP_ENGINE_VERSION,
        "fitted_at": datetime.now(timezone.utc).isoformat(),
        "seasons": sorted({c.season for c in contexts}),
        "validation_seasons": validation_seasons,
        "test_seasons": test_seasons,
        "parameters": params.to_dict(),
        "pace_scale_races": len(pairs),
        "search": sweep,
        "enabled": enabled,
        "metrics": {"test_lap": lap_test, "test_sampler": sampler_test},
        "profiles": profiles,
    }
    path = os.path.abspath(args.output)
    with open(path, "w") as handle:
        # Compact: thousands of strategy rows, one per car per race.
        json.dump(artifact, handle, sort_keys=True, separators=(",", ":"))
    logger.info("\nwrote %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
