#!/usr/bin/env python
"""Rolling season-by-season backtest of the race models, with error bars.

Usage:
    python scripts/backtest.py --from-api [--api-url http://localhost:8001] [--network]
    python scripts/backtest.py <results.jsonl> [--quali quali.jsonl] [--network]

For each season S in ``--seasons`` (default 2019-2025), every model is fitted on
the seasons before S only and scored on every race of S. Nothing in a fold has
seen S or anything after it, including the feature scaling and the linear model
the network is built on. That is the fix for the old single split, where the
linear model used as the yardstick had been fitted on the test seasons.

Models compared:

* ``uniform`` - every driver equally likely; the floor.
* ``linear`` - the Plackett-Luce model, refitted per fold.
* ``network`` - the hidden-layer correction on top of that fold's linear model,
  width and penalty chosen on the fold's last training season (``--network``).

Reported per season and pooled, with 95% bootstrap intervals over races, and a
paired network-minus-linear comparison: the network is only better if that
interval sits above zero. ``--json`` writes every per-race score.
"""

import argparse
import json
import logging
import os
import sys
from typing import Dict, List

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from app.services.model import feature_names  # noqa: E402
from app.training import backtest, dataset, neural, plackett_luce  # noqa: E402
from train_model import (  # noqa: E402
    TRAINING_FROM_SEASON,
    _load_practice,
    load_quali,
    load_results,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backtest")


def _linear(train, names) -> np.ndarray:
    weights = plackett_luce.fit(train, names)
    return np.array([weights[name] for name in names])


def linear_factory(train, names):
    vector = _linear(train, names)
    return lambda race: backtest.race_log_likelihood(vector, race)


def network_factory(train, names):
    """The network as ``train_neural.py`` builds it, inside one fold.

    The linear term is that fold's own linear fit, pinned; the hidden layer is
    fitted on the earlier seasons and chosen on the fold's last training season.
    """
    linear = _linear(train, names)
    last = max(o.season for o in train)
    inner = [o for o in train if o.season < last]
    validation = [o for o in train if o.season == last]
    if not inner or not validation:
        return lambda race: backtest.race_log_likelihood(linear, race)
    params, chosen, _ = neural.select(inner, validation, fixed_linear=linear)
    logger.info("  fold before %s: network hidden %s, l2 %s",
                last + 1, chosen["hidden"], chosen["l2_hidden"])
    return lambda race: neural.mean_log_likelihood(params, [race])


def _interval_dict(interval) -> Dict[str, float]:
    return dict(zip(("improvement", "low", "high"), interval))


def _percent(interval) -> str:
    point, low, high = interval
    return "{:6.2f}% [{:5.2f}, {:5.2f}]".format(100 * point, 100 * low, 100 * high)


def report(folds: List[backtest.FoldResult], labels: List[str]) -> Dict:
    summary: Dict = {"seasons": [], "pooled": {}}

    logger.info("\n=== improvement over uniform, 95% interval over races ===")
    header = "{:<7} {:>5} {:>6}".format("season", "races", "train")
    for label in labels:
        header += "   {:<24}".format(label)
    logger.info(header)

    for fold in folds:
        row = "{:<7} {:>5} {:>6}".format(fold.season, len(fold.uniform), fold.train_races)
        entry = {"season": fold.season, "races": len(fold.uniform),
                 "train_races": fold.train_races}
        for label in labels:
            interval = backtest.improvement_interval(fold.scores[label], fold.uniform)
            row += "   {:<24}".format(_percent(interval))
            entry[label] = _interval_dict(interval)
        logger.info(row)
        summary["seasons"].append(entry)

    row = "{:<7} {:>5} {:>6}".format("all", sum(len(f.uniform) for f in folds), "")
    for label in labels:
        interval = backtest.improvement_interval(*backtest.pooled(folds, label))
        row += "   {:<24}".format(_percent(interval))
        summary["pooled"][label] = _interval_dict(interval)
    logger.info(row)

    if "network" in labels:
        paired = backtest.paired_difference(
            backtest.pooled(folds, "network")[0], backtest.pooled(folds, "linear")[0]
        )
        summary["network_minus_linear"] = paired
        logger.info(
            "\nnetwork minus linear, log-likelihood per race: %+.3f [%+.3f, %+.3f]"
            " (network ahead in %.0f%% of resamples)",
            paired["mean"], paired["low"], paired["high"], 100 * paired["share_better"],
        )
        summary["verdict"] = backtest.verdict(paired)
        logger.info("verdict: %s", summary["verdict"])
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="?", default=None)
    parser.add_argument("--from-api", action="store_true")
    parser.add_argument("--api-url", default="http://localhost:8001")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--quali", default=None)
    parser.add_argument("--seasons", default="2019,2020,2021,2022,2023,2024,2025",
                        help="Seasons to hold out, one fold each.")
    parser.add_argument("--from-season", type=int, default=TRAINING_FROM_SEASON)
    parser.add_argument("--decay", type=float, default=0.6)
    parser.add_argument("--network", action="store_true",
                        help="Also backtest the hidden-layer network (slower).")
    parser.add_argument("--json", default=None, help="Write every per-race score here.")
    args = parser.parse_args()

    seasons = [int(s) for s in args.seasons.split(",") if s.strip()]

    practice = []
    if args.from_api:
        import asyncio
        from app.services.ingestion_client import IngestionClient
        from app.training import corpus

        client = IngestionClient(args.api_url)
        results, quali = asyncio.run(corpus.load(
            client, args.from_season, max(seasons),
            require_complete=not args.allow_incomplete,
        ))
        practice = asyncio.run(_load_practice(client, args.from_season, max(seasons)))
    elif args.results:
        results = load_results(args.results)
        quali = load_quali(args.quali)
    else:
        parser.error("pass a results JSONL or --from-api")

    usable = [row for row in results if row.season >= args.from_season]
    # Built once with raw features; each fold scales and weights its own copy.
    # The target season sets only era weights (recomputed per fold) and the
    # era bucket, which is "modern" for every season from 2011.
    observations = dataset.build_observations(
        usable, target_season=max(seasons), with_grid=True, decay=args.decay,
        quali=quali, practice=practice,
    )
    names = feature_names(with_grid=True)

    models = {"linear": linear_factory}
    if args.network:
        models["network"] = network_factory

    folds = backtest.rolling_folds(
        observations, names, seasons, models,
        decay=args.decay, from_season=args.from_season,
    )
    if not folds:
        raise SystemExit("no folds: check --seasons against the corpus")

    summary = report(folds, list(models))

    if args.json:
        payload = {
            "summary": summary,
            "folds": [
                {"season": f.season, "train_races": f.train_races, "rounds": f.rounds,
                 "uniform": f.uniform, "scores": f.scores}
                for f in folds
            ],
        }
        with open(args.json, "w") as handle:
            json.dump(payload, handle, indent=2)
        logger.info("\nwrote %s", os.path.abspath(args.json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
