#!/usr/bin/env python
"""Does a deeper network predict lap times better than the simulator does today?

Usage:
    python scripts/evaluate_lap_time_network.py [--api-url http://localhost:8001]
        [--tune 2024] [--test 2025,2026] [--test-last-round 19] [--write] [--json out.json]

Read-only against ingestion. Every clean lap from 2014 becomes a row
(``app/training/lap_time_network.py``): the lap's own state, what the race has
shown so far, and the race model's pre-race features for the driver.

1. **Choose** on the tuning season: each shape (linear, one and two hidden
   layers) and penalty is fitted on the seasons before it, with and without
   2014-2017 (no tyre data then), older seasons down-weighted by ``--decay``.
2. **Test** on seasons nothing was chosen on: the chosen network and the best
   linear model are refitted through the tuning season and scored per race
   against what the simulator does today: pre-race pace from the pace network,
   the circuit profile's tyre offset and wear, blended with the car's laps so
   far during the race. A paired bootstrap over races gives the interval.

The score is the error of each lap against the rest of the field on the same
lap, in percent of a lap. The network is marked enabled only when the interval
clears zero against the simulator. Nothing in serving reads the artifact yet;
the lap simulator is wired to it in the next stage.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import httpx
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from app.services import circuits, race_pace  # noqa: E402
from app.services.ingestion_client import IngestionClient  # noqa: E402
from app.services.live_replay import total_laps_of  # noqa: E402
from app.services.model import PACE_NETWORK_PATH, feature_names, feature_vector  # noqa: E402
from app.training import corpus, lap_profiles, tum_timing  # noqa: E402
from app.training.backtest import paired_bootstrap  # noqa: E402
from app.training.lap_time_network import (  # noqa: E402
    LAP_FEATURES,
    LAP_TIME_NETWORK_VERSION,
    LapTimeNetwork,
    centred,
    race_rows,
    race_weather,
    simulator_prediction,
)
from app.training.pace_network import PaceNetwork  # noqa: E402
from train_neural import calibration_races  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("evaluate_lap_time_network")

FIRST_SEASON = 2014
FIRST_TYRE_SEASON = 2018
FIRST_PACE_SEASON = 2009
ARTIFACT = os.path.join(os.path.dirname(__file__), "..", "app", "lap_time_network.json")
SHAPES = ((16,), (32, 16), (64, 32))
PENALTIES = (0.3, 3.0, 30.0)


async def _rows(api, name, seasons):
    out = []
    async with httpx.AsyncClient(timeout=120) as client:
        for season in seasons:
            response = await client.get(f"{api}/data/{name}",
                                        params={"season": season, "limit": 20000})
            response.raise_for_status()
            out.extend(response.json())
    return out


async def load(api, last):
    client = IngestionClient(api)
    results, quali, practice = [], [], []
    for season in range(FIRST_PACE_SEASON - 1, last + 1):
        results += await client.season_results(season)
        quali += await client.season_qualifying(season)
        practice += await client.season_practice(season)
    laps = {}
    for key in sorted({(r.season, r.round) for r in results if r.season >= FIRST_PACE_SEASON}):
        found = await client.race_laps(*key)
        if found:
            laps[key] = found
    contexts = await corpus.load_race_contexts(client, results)
    weather = race_weather(await _rows(api, "weather", range(FIRST_TYRE_SEASON, last + 1)))
    return results, quali, practice, laps, contexts, weather


class Table:
    """Every row of every race, as flat arrays."""

    def __init__(self):
        self.parts = defaultdict(list)

    def add(self, rows, driver_x, season):
        n = len(rows.y)
        self.parts["x"].append(np.hstack([rows.x, driver_x]))
        self.parts["y"].append(rows.y)
        self.parts["season"].append(np.full(n, season))
        self.parts["race"].append(np.full(n, rows.key[0] * 100 + rows.key[1]))
        self.parts["lap"].append(rows.laps)
        self.parts["live_laps"].append(rows.x[:, LAP_FEATURES.index("live_laps")])
        self.parts["share"].append(rows.x[:, LAP_FEATURES.index("race_share")])
        self.parts["wet"].append(np.array([c in ("INTERMEDIATE", "WET") for c in rows.compounds]))

    def done(self):
        for key, values in self.parts.items():
            setattr(self, key, np.concatenate(values))
        self.group = self.race * 1000 + self.lap
        return self


def _errors(prediction, table, mask):
    group = table.group[mask]
    return centred(prediction[mask], group) - centred(table.y[mask], group)


def per_race_error(prediction, table, mask):
    """Mean squared centred error per race (lower is better)."""
    error = _errors(prediction, table, mask)
    races = table.race[mask]
    return {(int(r) // 100, int(r) % 100): float(np.mean(error[races == r] ** 2))
            for r in np.unique(races)}


def rmse(prediction, table, mask):
    if not mask.any():
        return float("nan")
    return float(np.sqrt(np.mean(_errors(prediction, table, mask) ** 2)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:8001")
    parser.add_argument("--tune", type=int, default=2024)
    parser.add_argument("--test", default="2025,2026")
    parser.add_argument("--test-last-round", type=int, default=19,
                        help="rounds of the last test season after this are left out")
    parser.add_argument("--decay", type=float, default=0.85,
                        help="weight of a season relative to the one after it")
    parser.add_argument("--max-iter", type=int, default=600)
    parser.add_argument("--tum", default=None,
                        help="path to the TUM f1-timing-database SQLite file: tyre compound, "
                             "tyre age and SC/VSC laps for 2014-2017")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", default=None)
    args = parser.parse_args()
    tests = [int(s) for s in args.test.split(",") if s.strip()]
    last = max(tests)

    results, quali, practice, laps, contexts, weather = asyncio.run(load(args.api_url, last))
    archetypes = circuits.CircuitArchetypes.load()
    overlays = tum_timing.load(args.tum) if args.tum else {}
    tum_cover = defaultdict(list)
    circuit_of = {(r.season, r.round): r.circuit for r in results}
    by_race = defaultdict(list)
    for r in results:
        by_race[(r.season, r.round)].append(r)

    # Pre-race features per driver, as serving builds them on the final grid.
    drivers_of = {}
    for season in range(FIRST_PACE_SEASON, last + 1):
        for key, drivers, _, _ in calibration_races(results, [season], quali, practice,
                                                    with_keys=True):
            drivers_of[key] = drivers

    table = Table()
    race_rows_of = {}
    for key, race_laps in sorted(laps.items()):
        season, round_number = key
        if season < FIRST_SEASON or key not in drivers_of:
            continue
        if season == last and round_number > args.test_last_round:
            continue
        ages, neutral = {}, set()
        if key in overlays:
            race_laps, ages, neutral = tum_timing.apply(race_laps, overlays[key])
            tum_cover[season].append(tum_timing.coverage(race_laps, ages))
        rows = race_rows(season, round_number, race_laps,
                         total_laps_of(race_laps, by_race[key]),
                         archetype=archetypes.archetype_of(circuit_of.get(key, "")),
                         weather=weather.get(key), tyre_ages=ages, neutral=neutral)
        if rows is None:
            continue
        vectors = {d.driver: feature_vector(d, with_grid=True) for d in drivers_of[key]}
        keep = np.array([d in vectors for d in rows.drivers])
        if not keep.any():
            continue
        for name in ("x", "y", "laps", "ages"):
            setattr(rows, name, getattr(rows, name)[keep])
        rows.drivers = [d for d, k in zip(rows.drivers, keep) if k]
        rows.compounds = [c for c, k in zip(rows.compounds, keep) if k]
        table.add(rows, np.array([vectors[d] for d in rows.drivers]), season)
        race_rows_of[key] = rows
    table.done()
    for season, shares in sorted(tum_cover.items()):
        logger.info("TUM tyres %s: %s races, %.1f%% of laps matched", season, len(shares),
                    100 * float(np.mean(shares)))
    names = list(LAP_FEATURES) + ["pre_" + n for n in feature_names(with_grid=True)]
    logger.info("%s clean laps from %s races, %s inputs", len(table.y), len(race_rows_of),
                len(names))
    for season in sorted(set(table.season.tolist())):
        logger.info("  %s: %s laps", season, int((table.season == season).sum()))

    def fit(shape, l2, mask, reference):
        weights = args.decay ** (reference - table.season[mask])
        return LapTimeNetwork(hidden=shape, l2=l2, max_iter=args.max_iter).fit(
            table.x[mask], table.y[mask], weights)

    # 1. Choose on the tuning season.
    tune = table.season == args.tune
    candidates = []
    for first in (FIRST_SEASON, FIRST_TYRE_SEASON):
        train = (table.season >= first) & (table.season < args.tune)
        for shape, l2 in [((), 0.0)] + [(s, p) for s in SHAPES for p in PENALTIES]:
            net = fit(shape, l2, train, args.tune - 1)
            score = rmse(net.predict(table.x), table, tune & ~table.wet)
            candidates.append({"from": first, "hidden": list(shape), "l2": l2,
                               "tune_rmse": score})
            logger.info("from %s  hidden %-8s l2 %-5s  tuning RMSE %.4f%%", first,
                        str(shape), l2, score)
    linear = min((c for c in candidates if not c["hidden"]), key=lambda c: c["tune_rmse"])
    deep = min((c for c in candidates if c["hidden"]), key=lambda c: c["tune_rmse"])
    logger.info("chosen: network %s; linear from %s", deep, linear["from"])

    # 2. Test, refitted through the tuning season.
    test = np.isin(table.season, tests)
    through = table.season <= args.tune
    networks = {
        "network": fit(tuple(deep["hidden"]), deep["l2"],
                       through & (table.season >= deep["from"]), args.tune),
        "linear": fit((), 0.0, through & (table.season >= linear["from"]), args.tune),
    }
    predictions = {label: net.predict(table.x) for label, net in networks.items()}

    # Today's simulator: pace network fitted on driver-races before the first
    # test season, circuit profiles from races before it.
    cut = min(tests) * 100 + 1
    pace_artifact = {}
    if os.path.exists(PACE_NETWORK_PATH):
        with open(PACE_NETWORK_PATH) as handle:
            pace_artifact = json.load(handle)
    scale = float(pace_artifact.get("pace_scale", 1.5))
    pace_rows = []
    for key, drivers in sorted(drivers_of.items()):
        gaps = race_pace.race_gaps(laps[key]) if key in laps else {}
        for d in drivers:
            pace_rows.append((key, d.driver, feature_vector(d, with_grid=True),
                              gaps.get(d.driver)))
    order = np.array([k[0] * 100 + k[1] for k, _, _, _ in pace_rows])
    px = np.array([v for _, _, v, _ in pace_rows])
    py = np.array([100 * g if g is not None else np.nan for _, _, _, g in pace_rows])
    known = (order < cut) & ~np.isnan(py)
    pace_net = PaceNetwork().fit(px[known], py[known], order[known])
    prior_of = defaultdict(dict)
    for (key, driver, _, _), value in zip(pace_rows, pace_net.predict(px, order)):
        prior_of[key][driver] = scale * float(value)
    profiles = lap_profiles.build_profiles(
        [c for c in contexts if c.season * 100 + c.round < cut])
    simulator = np.zeros(len(table.y))
    offset = 0
    for key, rows in race_rows_of.items():
        n = len(rows.y)
        if key[0] in tests:
            profile = lap_profiles.profile_for(profiles, circuit_of.get(key, ""))
            simulator[offset:offset + n] = simulator_prediction(rows, prior_of[key], profile)
        offset += n
    predictions["simulator"] = simulator

    dry = test & ~table.wet
    report = {"candidates": candidates, "chosen": {"network": deep, "linear": linear},
              "test_seasons": tests, "tune_season": args.tune, "decay": args.decay,
              "tum": bool(args.tum),
              "rmse": {}}
    logger.info("\nTest (%s, dry laps), centred RMSE in %% of a lap:", args.test)
    slices = {
        "all": dry,
        "before any clean lap": dry & (table.live_laps == 0),
        "first third": dry & (table.share < 1 / 3),
        "middle third": dry & (table.share >= 1 / 3) & (table.share < 2 / 3),
        "last third": dry & (table.share >= 2 / 3),
        "wet laps": test & table.wet,
    }
    for name, mask in slices.items():
        if not mask.any():
            continue
        row = {label: rmse(p, table, mask) for label, p in predictions.items()}
        row["laps"] = int(mask.sum())
        report["rmse"][name] = row
        logger.info("  %-22s simulator %.4f   linear %.4f   network %.4f   (%s laps)", name,
                    row["simulator"], row["linear"], row["network"], row["laps"])

    # Lower error is better, so scores are negated: a positive gain is better.
    per_race = {label: {k: -v for k, v in per_race_error(p, table, dry).items()}
                for label, p in predictions.items()}
    comparisons = {
        "network vs simulator": paired_bootstrap(per_race["simulator"], per_race["network"]),
        "linear vs simulator": paired_bootstrap(per_race["simulator"], per_race["linear"]),
        "network vs linear": paired_bootstrap(per_race["linear"], per_race["network"]),
    }
    report["comparisons"] = comparisons
    for name, result in comparisons.items():
        logger.info("  %-22s gain %+.5f (95%% CI %+.5f .. %+.5f), better in %.0f%% of %s races",
                    name, result["mean"], result["low"], result["high"],
                    100 * result["better_share"], result["races"])
    enabled = comparisons["network vs simulator"]["low"] > 0
    verdict = ("the network predicts laps better than the simulator" if enabled
               else "keep the simulator's lap model: the interval does not clear zero")
    report["verdict"] = verdict
    logger.info("verdict: %s", verdict)

    if args.json:
        with open(args.json, "w") as handle:
            json.dump(report, handle, indent=2, default=float)
    if not args.write:
        logger.info("\n(pass --write to save the artifact, refitted on every lap through the "
                    "last test round)")
        return 0
    everything = table.season >= deep["from"]
    final = fit(tuple(deep["hidden"]), deep["l2"], everything, last)
    artifact = {
        "version": LAP_TIME_NETWORK_VERSION,
        "fitted_at": datetime.now(timezone.utc).isoformat(),
        "fitted_through": [last, args.test_last_round],
        "laps": int(everything.sum()),
        "feature_names": names,
        "decay": args.decay,
        "enabled": bool(enabled),
        "metrics": {k: report[k] for k in ("rmse", "comparisons", "verdict", "chosen")},
        "network": final.to_dict(),
    }
    path = os.path.abspath(ARTIFACT)
    with open(path, "w") as handle:
        json.dump(artifact, handle, sort_keys=True, separators=(",", ":"), default=float)
    logger.info("wrote %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
