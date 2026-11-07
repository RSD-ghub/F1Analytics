#!/usr/bin/env python
"""Replay finished races lap by lap and score the live forecast.

Usage:
    python scripts/backtest_live.py [--api-url http://localhost:8001]
        [--validation-seasons 2022,2023] [--test-seasons 2024,2025]
        [--runs 1000] [--drop-laps 0.0] [--report live_backtest.json]

Read-only: it reads the ingestion API and, with ``--report``, writes one JSON
file. Nothing is changed in the database or the model artifacts.

**How it is kept honest.** The strength model is fitted only on seasons before
the first validation season, and the circuit profiles are built only from those
races too, the same split as ``fit_lap_simulation.py``. The simulator's
fitted constants come from the lap simulation artifact, which was fitted on
2018-2021. At each checkpoint the race state is built from the laps up to that
lap only (``live_state.race_state``).

**What it fits.** ``prior_laps``, how many clean laps of a car's own running
count as much as its pre-race pace, is chosen on the validation seasons. The
test seasons are then scored once with it.

**What it prints**, per checkpoint (share of race distance): the live forecast
against the pre-race forecast and against "the order holds", on winner log
loss, favourite-won rate, podium Brier and expected-place error, and the paired
difference with a 95% bootstrap interval over races. Wet races (any car on
intermediates or wets) are scored apart, since the simulator only knows dry
tyres.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from app.services import live_forecast  # noqa: E402
from app.services.ingestion_client import IngestionClient  # noqa: E402
from app.services.lap_simulation import LapSimulationParameters  # noqa: E402
from app.services.live_state import WET_COMPOUNDS, race_state  # noqa: E402
from app.services.model import LAP_SIMULATION_PATH, RaceModel, feature_names  # noqa: E402
from app.training import corpus, dataset, lap_profiles, live_backtest, plackett_luce, retirements  # noqa: E402
from train_model import TRAINING_FROM_SEASON, _load_practice  # noqa: E402
from train_neural import calibration_races  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("backtest_live")

PRIOR_LAPS_CANDIDATES = (3.0, 10.0, 30.0)


class Race:
    """One finished race, with everything a replay needs."""

    def __init__(self, key, drivers, actual, seed, laps, stops, messages, results, circuit):
        self.key = key
        self.drivers = drivers
        self.actual = actual
        self.seed = seed
        self.laps = laps
        self.stops = stops
        self.messages = messages
        self.results = results
        self.circuit = circuit
        winner = next((d.driver for d, a in zip(drivers, actual) if a == 1), None)
        own = [lap.lap for lap in laps if lap.driver == winner]
        self.total_laps = max(own or [lap.lap for lap in laps] or [0])
        self.wet = any((lap.compound or "").upper() in WET_COMPOUNDS for lap in laps)


async def load_races(client, results, quali, practice, seasons):
    races = []
    for key, drivers, actual, seed in calibration_races(
            results, seasons, quali, practice, with_keys=True):
        season, round_number = key
        laps = await client.race_laps(season, round_number)
        if not laps:
            logger.info("  %s: no laps, skipped", key)
            continue
        races.append((key, drivers, actual, seed, laps))
    stops, messages = defaultdict(list), defaultdict(list)
    for season in seasons:
        for row in await client.season_pit_stops(season):
            stops[(season, row.round)].append(row)
        for row in await client.season_race_control(season):
            messages[(season, row.round)].append(row)
    by_race = defaultdict(list)
    for row in results:
        by_race[(row.season, row.round)].append(row)
    out = []
    for key, drivers, actual, seed, laps in races:
        circuit = next((r.circuit for r in by_race[key] if r.circuit), "")
        out.append(Race(key, drivers, actual, seed, laps, stops[key], messages[key],
                        by_race[key], circuit))
    return out


def replay(race, model, profiles, params, prior_laps, runs, drop_rate, pre_race_cache):
    """Score rows for one race at every checkpoint."""
    profile = lap_profiles.profile_for(profiles, race.circuit)
    strengths = model.strengths(race.drivers)
    names = [d.driver for d in race.drivers]
    laps = live_backtest.drop_laps(race.laps, drop_rate, np.random.default_rng(race.seed))
    kwargs = dict(retirement_race_k=model.retirement_race_k,
                  retirement_team_k=model.retirement_team_k)
    if race.key not in pre_race_cache:
        pre_profile = dict(profile, laps=race.total_laps)
        pre_race_cache[race.key] = live_forecast.forecast(
            race.drivers, strengths, pre_profile, params, None,
            seed=race.seed, runs=runs, **kwargs)
    pre = pre_race_cache[race.key]
    rows = []
    for fraction, lap in zip(live_backtest.CHECKPOINTS,
                             live_backtest.checkpoint_laps(race.total_laps)):
        def add(method, outcome):
            row = live_backtest.score(*outcome, race.actual)
            row.update(race=race.key, method=method, checkpoint=fraction, wet=race.wet)
            rows.append(row)

        add("pre_race", pre)
        if lap < 1:
            add("live", pre)
            continue
        state = race_state(names, laps, race.stops, race.messages, race.results,
                           upto=lap, total_laps=race.total_laps, profile=profile)
        add("order_holds", live_backtest.order_holds(state))
        add("live", live_forecast.forecast(
            race.drivers, strengths, profile, params, state,
            seed=race.seed + lap, runs=runs, prior_laps=prior_laps, **kwargs))
    return rows


def run_all(races, model, profiles, params, prior_laps, runs, drop_rate):
    cache, rows = {}, []
    for race in races:
        rows.extend(replay(race, model, profiles, params, prior_laps, runs, drop_rate, cache))
    return rows


def report(rows, title):
    logger.info("\n=== %s ===", title)
    summary = live_backtest.summarise(rows)
    header = "%-9s %-12s %6s %10s %8s %8s %9s"
    logger.info(header, "distance", "method", "races", "win LL", "fav won", "podium",
                "place err")
    for (method, checkpoint), m in sorted(summary.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        logger.info("%-9s %-12s %6d %10.3f %8.2f %8.3f %9.2f", "{:.0%}".format(checkpoint),
                    method, m["races"], m["winner_log_loss"], m["winner_hit"],
                    m["podium_brier"], m["position_error"])
    logger.info("\nlive minus baseline (negative = live better), 95%% interval over races:")
    for checkpoint in live_backtest.CHECKPOINTS[1:]:
        for baseline in ("pre_race", "order_holds"):
            parts = []
            for measure in ("winner_log_loss", "podium_brier"):
                mean, (low, high), count = live_backtest.paired_difference(
                    rows, "live", baseline, checkpoint, measure)
                parts.append("{} {:+.3f} [{:+.3f}, {:+.3f}]".format(measure, mean, low, high))
            logger.info("  %-4s vs %-12s %s", "{:.0%}".format(checkpoint), baseline,
                        "   ".join(parts))
    return {"{}@{}".format(m, c): v for (m, c), v in summary.items()}


def objective(rows):
    """Validation score for choosing prior_laps: winner log loss plus podium
    Brier, averaged over checkpoints after the start, dry races only."""
    summary = live_backtest.summarise([r for r in rows if r["method"] == "live"
                                       and r["checkpoint"] > 0 and not r["wet"]])
    return float(np.mean([m["winner_log_loss"] + m["podium_brier"] for m in summary.values()]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:8001")
    parser.add_argument("--validation-seasons", default="2022,2023")
    parser.add_argument("--test-seasons", default="2024,2025")
    parser.add_argument("--runs", type=int, default=1000)
    parser.add_argument("--drop-laps", type=float, default=0.0,
                        help="share of lap times to blank, to test a flaky feed")
    parser.add_argument("--prior-laps", type=float, default=None,
                        help="skip the validation search and use this value")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--artifact", default=LAP_SIMULATION_PATH)
    parser.add_argument("--report", default=None, help="write a JSON report here")
    args = parser.parse_args()
    validation_seasons = [int(s) for s in args.validation_seasons.split(",") if s]
    test_seasons = [int(s) for s in args.test_seasons.split(",") if s]
    first_held_out = min(validation_seasons + test_seasons)
    last = max(validation_seasons + test_seasons)

    with open(args.artifact) as handle:
        params = LapSimulationParameters.from_dict(json.load(handle)["parameters"])

    client = IngestionClient(args.api_url)
    results, quali = asyncio.run(corpus.load(
        client, TRAINING_FROM_SEASON, last, require_complete=not args.allow_incomplete))
    practice = asyncio.run(_load_practice(client, TRAINING_FROM_SEASON, last))

    # A strength model and circuit profiles that have seen nothing held out.
    observations = dataset.build_observations(
        results, target_season=last + 1, with_grid=True, quali=quali, practice=practice)
    names = feature_names(with_grid=True)
    means, stds = dataset.standardise(observations, names)
    train = [o for o in observations if o.season < first_held_out]
    model = RaceModel(weights=plackett_luce.fit(train, names), means=means, stds=stds,
                      noise_scale=0.3)
    model.retirement_race_k, model.retirement_team_k, _ = retirements.fit(
        [r for r in results if r.season < first_held_out])
    early_results = [r for r in results if r.season < first_held_out]
    contexts = asyncio.run(corpus.load_race_contexts(client, early_results))
    profiles = lap_profiles.build_profiles(contexts)
    params.overall_orderliness = profiles["overall"]["orderliness"]

    output = {"runs": args.runs, "drop_laps": args.drop_laps,
              "validation_seasons": validation_seasons, "test_seasons": test_seasons}
    prior_laps = args.prior_laps
    if prior_laps is None and validation_seasons:
        validation = asyncio.run(load_races(client, results, quali, practice, validation_seasons))
        logger.info("\nchoosing prior_laps on %s (%s races):", validation_seasons, len(validation))
        search = {}
        for candidate in PRIOR_LAPS_CANDIDATES:
            rows = run_all(validation, model, profiles, params, candidate, args.runs, 0.0)
            search[candidate] = objective(rows)
            logger.info("  prior_laps %5.1f   score %.4f", candidate, search[candidate])
        prior_laps = min(search, key=search.get)
        output["search"] = {str(k): v for k, v in search.items()}
    prior_laps = prior_laps or live_forecast.DEFAULT_PRIOR_LAPS
    output["prior_laps"] = prior_laps
    logger.info("prior_laps: %.1f", prior_laps)

    test = asyncio.run(load_races(client, results, quali, practice, test_seasons))
    rows = run_all(test, model, profiles, params, prior_laps, args.runs, args.drop_laps)
    dry = [r for r in rows if not r["wet"]]
    wet = [r for r in rows if r["wet"]]
    output["dry"] = report(dry, "held-out {} dry races ({} races)".format(
        test_seasons, len({r["race"] for r in dry})))
    if wet:
        output["wet"] = report(wet, "held-out {} wet races ({} races)".format(
            test_seasons, len({r["race"] for r in wet})))
    if args.report:
        with open(args.report, "w") as handle:
            json.dump(output, handle, indent=2, sort_keys=True)
        logger.info("\nwrote %s", os.path.abspath(args.report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
