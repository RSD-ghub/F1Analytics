#!/usr/bin/env python
"""Replay finished races lap by lap and score the live forecast.

Usage:
    python scripts/backtest_live.py [--api-url http://localhost:8001]
        [--validation-seasons 2022,2023] [--test-seasons 2024,2025]
        [--runs 1000] [--drop-laps 0.0] [--report live_backtest.json]
        [--network [--write-network [path]]]

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

**The live network** (``--network``, ``app/training/live_network.py``) is
trained on replays of the validation seasons at every 5% of distance, its
penalty chosen by holding out one validation season at a time, and scored on
the test seasons as ``live_net`` beside the simulator alone. It is enabled only
if, per race, it beats the simulator alone on winner log loss plus podium
Brier with a 95% interval clear of zero. ``--write-network`` writes the
artifact either way, with that verdict in ``enabled``.

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
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from app.services import live_forecast  # noqa: E402
from app.services.ingestion_client import IngestionClient  # noqa: E402
from app.services.lap_simulation import LapSimulationParameters  # noqa: E402
from app.services.live_state import WET_COMPOUNDS, race_state  # noqa: E402
from app.services.model import LAP_SIMULATION_PATH, RaceModel, feature_names  # noqa: E402
from app.training import corpus, dataset, lap_profiles, live_backtest, plackett_luce, retirements  # noqa: E402
from app.training import live_network  # noqa: E402
from train_model import TRAINING_FROM_SEASON, _load_practice  # noqa: E402
from train_neural import calibration_races  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("backtest_live")

PRIOR_LAPS_CANDIDATES = (1.0, 3.0, 10.0, 30.0)
#: Where the network is trained: every 5% of race distance.
TRAIN_FRACTIONS = tuple(round(0.05 * i, 2) for i in range(1, 20))
LIVE_NETWORK_PATH = os.path.join(os.path.dirname(LAP_SIMULATION_PATH), "live_network.json")


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


def live_sample(race, state, outcome, strengths, params, prior_laps, profile):
    """The network's view of one race at one checkpoint, and what happened."""
    positions, retired = outcome
    pace, _ = live_forecast.posterior_pace(live_forecast.prior_pace(strengths, params),
                                           state, params.race_day_sd, prior_laps)
    sim_win = ((positions == 1) & ~retired).mean(axis=0)
    sim_podium = ((positions <= live_backtest.PODIUM) & ~retired).mean(axis=0)
    x = live_network.features(state, pace, positions.mean(axis=0), retired.mean(axis=0), profile)
    actual = np.asarray(race.actual)
    finished = (actual >= 1) & (actual <= len(actual))
    winner = int(np.flatnonzero(actual == 1)[0]) if (actual == 1).any() else -1
    return live_network.Sample(x, sim_win, sim_podium, ~state.retired, winner,
                               (finished & (actual <= live_backtest.PODIUM)).astype(float),
                               group=race.key[0])


def network_samples(races, model, profiles, params, prior_laps, runs):
    """Replays at every 5% of distance, as training rows for the network."""
    samples = []
    for race in races:
        profile = lap_profiles.profile_for(profiles, race.circuit)
        strengths = model.strengths(race.drivers)
        names = [d.driver for d in race.drivers]
        for lap in live_backtest.checkpoint_laps(race.total_laps, TRAIN_FRACTIONS):
            if lap < 1:
                continue
            state = race_state(names, race.laps, race.stops, race.messages, race.results,
                               upto=lap, total_laps=race.total_laps, profile=profile)
            outcome = live_forecast.forecast(
                race.drivers, strengths, profile, params, state, seed=race.seed + lap,
                runs=runs, prior_laps=prior_laps,
                retirement_race_k=model.retirement_race_k,
                retirement_team_k=model.retirement_team_k)
            samples.append(live_sample(race, state, outcome, strengths, params,
                                       prior_laps, profile))
    return samples


def replay(race, model, profiles, params, prior_laps, runs, drop_rate, pre_race_cache,
           network=None):
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
        outcome = live_forecast.forecast(
            race.drivers, strengths, profile, params, state,
            seed=race.seed + lap, runs=runs, prior_laps=prior_laps, **kwargs)
        add("live", outcome)
        if network is not None:
            sample = live_sample(race, state, outcome, strengths, params, prior_laps, profile)
            win, podium = network.predict(sample.x, sample.sim_win, sample.sim_podium,
                                          sample.running)
            row = live_backtest.score_probabilities(win, podium, outcome[0].mean(axis=0),
                                                    race.actual)
            row.update(race=race.key, method="live_net", checkpoint=fraction, wet=race.wet)
            rows.append(row)
    return rows


def run_all(races, model, profiles, params, prior_laps, runs, drop_rate, network=None):
    cache, rows = {}, []
    for race in races:
        rows.extend(replay(race, model, profiles, params, prior_laps, runs, drop_rate, cache,
                           network))
    return rows


def network_gate(rows, resamples=2000, seed=0):
    """Live with the network minus live alone, on winner log loss plus podium
    Brier averaged over the checkpoints after the start, per race; with a 95%
    bootstrap interval over races. The network is enabled only if the whole
    interval is below zero."""
    per_race = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row["checkpoint"] > 0 and row["method"] in ("live", "live_net") and not row["wet"]:
            per_race[row["race"]][row["method"]].append(
                row["winner_log_loss"] + row["podium_brier"])
    diffs = np.array([np.mean(v["live_net"]) - np.mean(v["live"])
                      for v in per_race.values() if v["live"] and v["live_net"]])
    if not diffs.size:
        return float("nan"), (float("nan"), float("nan")), False
    rng = np.random.default_rng(seed)
    means = diffs[rng.integers(0, diffs.size, size=(resamples, diffs.size))].mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return float(diffs.mean()), (float(low), float(high)), bool(high < 0)


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
    logger.info("\nmethod minus baseline (negative = method better), 95% interval over races:")
    for checkpoint in live_backtest.CHECKPOINTS[1:]:
        methods = {r["method"] for r in rows}
        pairs = [("live", b) for b in ("pre_race", "order_holds")]
        if "live_net" in methods:
            pairs += [("live_net", "live"), ("live_net", "order_holds")]
        for method, baseline in pairs:
            parts = []
            for measure in ("winner_log_loss", "podium_brier"):
                mean, (low, high), count = live_backtest.paired_difference(
                    rows, method, baseline, checkpoint, measure)
                parts.append("{} {:+.3f} [{:+.3f}, {:+.3f}]".format(measure, mean, low, high))
            logger.info("  %-4s %-8s vs %-12s %s", "{:.0%}".format(checkpoint), method,
                        baseline, "   ".join(parts))
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
    parser.add_argument("--network", action="store_true",
                        help="train the live network on the validation seasons and score it")
    parser.add_argument("--write-network", default=None, nargs="?", const=LIVE_NETWORK_PATH,
                        help="with --network, write the network artifact (default %(const)s)")
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
    validation = None
    if (prior_laps is None or args.network) and validation_seasons:
        validation = asyncio.run(load_races(client, results, quali, practice, validation_seasons))
    if prior_laps is None and validation:
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

    network = None
    if args.network:
        if not validation:
            logger.error("--network needs validation seasons to train on")
            return 2
        samples = network_samples(validation, model, profiles, params, prior_laps, args.runs)
        l2, search, simulator_loss = live_network.choose_l2(samples)
        logger.info("\nlive network on %s samples from %s races; held-out loss by season "
                    "(simulator alone %.4f):", len(samples), len(validation), simulator_loss)
        for value, loss in search.items():
            logger.info("  l2 %6.1f   %.4f", value, loss)
        network = live_network.LiveNetwork(l2=l2).fit(samples)
        output["network_search"] = {str(k): v for k, v in search.items()}
        output["network_simulator_loss"] = simulator_loss
        output["network_l2"] = l2

    test = asyncio.run(load_races(client, results, quali, practice, test_seasons))
    rows = run_all(test, model, profiles, params, prior_laps, args.runs, args.drop_laps,
                   network)
    dry = [r for r in rows if not r["wet"]]
    wet = [r for r in rows if r["wet"]]
    output["dry"] = report(dry, "held-out {} dry races ({} races)".format(
        test_seasons, len({r["race"] for r in dry})))
    if wet:
        output["wet"] = report(wet, "held-out {} wet races ({} races)".format(
            test_seasons, len({r["race"] for r in wet})))
    if network is not None:
        mean, (low, high), enabled = network_gate(rows)
        logger.info("\nnetwork gate (dry, live_net minus live, winner log loss + podium Brier "
                    "per race): %+.4f [%+.4f, %+.4f] -> %s", mean, low, high,
                    "ENABLE" if enabled else "keep the simulator alone")
        output["network_gate"] = {"mean": mean, "low": low, "high": high, "enabled": enabled}
        if args.write_network:
            artifact = {
                "version": live_network.LIVE_NETWORK_VERSION,
                "fitted_at": datetime.now(timezone.utc).isoformat(),
                "training_seasons": validation_seasons,
                "test_seasons": test_seasons,
                "prior_laps": prior_laps,
                "enabled": enabled,
                "gate": output["network_gate"],
                "network": network.to_dict(),
            }
            with open(args.write_network, "w") as handle:
                json.dump(artifact, handle, sort_keys=True)
            logger.info("wrote %s", os.path.abspath(args.write_network))
    if args.report:
        with open(args.report, "w") as handle:
            json.dump(output, handle, indent=2, sort_keys=True)
        logger.info("\nwrote %s", os.path.abspath(args.report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
