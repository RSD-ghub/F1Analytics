#!/usr/bin/env python
"""Could the platform forecast qualifying? Measure it before building it.

Usage:
    python scripts/backtest_quali_market.py --from-api [--api-url http://localhost:8001]
    python scripts/backtest_quali_market.py <results.jsonl> --quali quali.jsonl

A qualifying forecast would publish, before Saturday, each driver's chance of
pole, of a top-three start and of reaching Q3 (top ten). This is the backtest
that has to come first. It uses exactly the pre-quali features the race model
sees (``build_snapshot`` for the pre-quali window: earlier weekends' qualifying
gaps and this weekend's practice, never this weekend's qualifying), and
refits the Plackett-Luce model on the qualifying classification instead of
the race result.

Rolling season folds as in ``backtest.py``: each season S is scored by a fit
on seasons before S only, with the temperature chosen on the fold's last
training season. Chances for top three and Q3 come from sampling the fitted
model (Gumbel noise on the utilities).

Two models, so a skilful-looking number can be told apart from a trivial one:

* ``model`` - every pre-quali feature.
* ``gap_only`` - only ``recent_quali_gap_pct``, the rolling qualifying pace.
  If ``model`` cannot beat this, a qualifying forecast is a ranking of last
  month's qualifying gaps and needs no model of its own.

Scores: Brier skill over a uniform guess (k/field) per market, with 95%
bootstrap intervals over weekends, and the paired model-minus-gap_only
difference in summed Brier. Nothing here writes or changes what is served.
"""

import argparse
import json
import logging
import os
import sys
from typing import Callable, Dict, List, Sequence

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from app.services.model import feature_names  # noqa: E402
from app.training import backtest, dataset  # noqa: E402
from app.training.plackett_luce import RaceObservation  # noqa: E402
from backtest_win_market import _fit, choose_temperature  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backtest_quali_market")

#: Market name and how many places make it true.
MARKETS = (("pole", 1), ("top3", 3), ("q3", 10))
#: Samples per weekend for the top-three and Q3 chances.
RUNS = 2000
#: Fewer classified qualifiers than this and the weekend is skipped.
MIN_CLASSIFIED = 10
GAP_FEATURE = "recent_quali_gap_pct"


def requalify(observations: Sequence[RaceObservation], quali) -> List[RaceObservation]:
    """The same feature rows, ranked by the weekend's qualifying classification
    instead of the race result. Weekends without enough qualifying rows go."""
    places: Dict = {}
    for row in quali:
        if 0 < row.position < 999:
            places.setdefault((row.season, row.round), {})[row.driver] = row.position
    out = []
    for o in observations:
        weekend = places.get((o.season, o.round), {})
        ranked = sorted((weekend[d], i) for i, d in enumerate(o.drivers or []) if d in weekend)
        if len(ranked) < MIN_CLASSIFIED:
            continue
        out.append(RaceObservation(o.features, [i for _, i in ranked], season=o.season,
                                   round_number=o.round, weight=o.weight, drivers=o.drivers))
    return out


def place_chances(weights: np.ndarray, race: RaceObservation, temperature: float,
                  runs: int = RUNS, seed: int = 0) -> Dict[str, np.ndarray]:
    """Each driver's chance of each market, from sampled Plackett-Luce orders."""
    utility = race.features @ weights / temperature
    rng = np.random.default_rng(seed + 1000 * race.season + race.round)
    noisy = utility + rng.gumbel(size=(runs, len(utility)))
    ranks = np.argsort(np.argsort(-noisy, axis=1), axis=1)  # 0 = pole
    return {name: (ranks < k).mean(axis=0) for name, k in MARKETS}


def score_weekend(chances: Dict[str, np.ndarray], race: RaceObservation) -> Dict[str, List[float]]:
    """{market: [model Brier, uniform Brier]} for one weekend."""
    n = race.features.shape[0]
    out = {}
    for name, k in MARKETS:
        outcome = np.zeros(n)
        outcome[race.order[:k]] = 1.0
        uniform = np.full(n, min(k / n, 1.0))
        out[name] = [float(((chances[name] - outcome) ** 2).sum()),
                     float(((uniform - outcome) ** 2).sum())]
    return out


def factory(columns: Sequence[int] = None) -> Callable:
    """A fold model on all columns, or only ``columns``."""
    def build(train: List[RaceObservation], names: Sequence[str]):
        keep = list(range(len(names))) if columns is None else list(columns)
        sub_names = [names[i] for i in keep]

        def cut(o):
            return RaceObservation(o.features[:, keep], o.order, season=o.season,
                                   round_number=o.round, weight=o.weight)

        sub = [cut(o) for o in train]
        weights = _fit(sub, sub_names)
        temperature = choose_temperature(sub, sub_names)
        return lambda race: score_weekend(
            place_chances(weights, cut(race), temperature), race)
    return build


def summarise(folds: List[backtest.FoldResult]) -> Dict:
    labels = list(folds[0].scores)

    def skills(scores):
        out = {}
        for name, _ in MARKETS:
            point, low, high = backtest.improvement_interval(
                [-s[name][0] for s in scores], [-s[name][1] for s in scores])
            out[name] = {"skill": point, "low": low, "high": high}
        return out

    summary: Dict = {"seasons": [], "pooled": {}}
    for fold in folds:
        entry = {"season": fold.season, "weekends": len(fold.rounds)}
        for label in labels:
            entry[label] = skills(fold.scores[label])
        summary["seasons"].append(entry)
    pooled = {label: [s for f in folds for s in f.scores[label]] for label in labels}
    for label in labels:
        summary["pooled"][label] = skills(pooled[label])
    summary["weekends"] = len(pooled[labels[0]])
    if "gap_only" in labels:
        total = lambda s: sum(v[0] for v in s.values())  # noqa: E731
        paired = backtest.paired_difference(
            [-total(s) for s in pooled["model"]], [-total(s) for s in pooled["gap_only"]])
        summary["model_minus_gap_only"] = paired
        summary["verdict"] = verdict(summary["pooled"]["model"], paired)
    return summary


def verdict(model: Dict, paired: Dict) -> str:
    """Worth building only if it beats a guess on every market and beats the
    plain qualifying-pace ranking."""
    weak = [name for name, _ in MARKETS if model[name]["low"] <= 0]
    if weak:
        return "not worth building: no clear skill on " + ", ".join(weak)
    if paired["low"] <= 0:
        return ("skilful, but not clearly better than ranking recent qualifying gaps "
                "(paired {:+.3f} [{:+.3f}, {:+.3f}])".format(
                    paired["mean"], paired["low"], paired["high"]))
    return "worth building: skilful on every market and better than qualifying gaps alone"


def report(summary: Dict) -> None:
    labels = list(summary["pooled"])
    for label in labels:
        logger.info("\n=== %s: Brier skill over uniform, 95%% interval over weekends ===", label)
        logger.info("{:<7} {:>4}".format("season", "n") + "".join(
            "   {:<24}".format(name) for name, _ in MARKETS))
        rows = [(s["season"], s["weekends"], s[label]) for s in summary["seasons"]]
        rows.append(("all", summary["weekends"], summary["pooled"][label]))
        for season, n, cells in rows:
            logger.info("{:<7} {:>4}".format(season, n) + "".join(
                "   {:+6.1%} [{:+6.1%}, {:+6.1%}]".format(
                    cells[name]["skill"], cells[name]["low"], cells[name]["high"])
                for name, _ in MARKETS))
    if "verdict" in summary:
        p = summary["model_minus_gap_only"]
        logger.info("\nmodel minus gap_only, summed Brier per weekend (higher is better): "
                    "%+.3f [%+.3f, %+.3f]", p["mean"], p["low"], p["high"])
        logger.info("verdict: %s", summary["verdict"])


def run_observations(observations: Sequence[RaceObservation], seasons: Sequence[int],
                     from_season: int, decay: float) -> Dict:
    names = feature_names(with_grid=False)
    models = {"model": factory(), "gap_only": factory([names.index(GAP_FEATURE)])}
    folds = backtest.rolling_folds(observations, names, seasons, models,
                                   decay=decay, from_season=from_season)
    if not folds:
        raise SystemExit("no folds: check --seasons and that qualifying is ingested")
    return summarise(folds)


def main() -> int:
    from train_model import TRAINING_FROM_SEASON, _load_practice, load_quali, load_results

    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="?", default=None)
    parser.add_argument("--from-api", action="store_true")
    parser.add_argument("--api-url", default="http://localhost:8001")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--quali", default=None)
    parser.add_argument("--seasons", default="2019,2020,2021,2022,2023,2024,2025")
    parser.add_argument("--from-season", type=int, default=TRAINING_FROM_SEASON)
    parser.add_argument("--decay", type=float, default=0.6)
    parser.add_argument("--json", default=None, help="Write the summary here.")
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
            require_complete=not args.allow_incomplete))
        practice = asyncio.run(_load_practice(client, args.from_season, max(seasons)))
    elif args.results and args.quali:
        results = load_results(args.results)
        quali = load_quali(args.quali)
    else:
        parser.error("pass --from-api, or a results JSONL with --quali")

    usable = [row for row in results if row.season >= args.from_season]
    observations = requalify(dataset.build_observations(
        usable, target_season=max(seasons), with_grid=False, decay=args.decay,
        quali=quali, practice=practice), quali)
    logger.info("%s weekends with a qualifying classification", len(observations))
    summary = run_observations(observations, seasons, args.from_season, args.decay)
    report(summary)
    if args.json:
        with open(args.json, "w") as handle:
            json.dump(summary, handle, indent=2)
        logger.info("\nwrote %s", os.path.abspath(args.json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
