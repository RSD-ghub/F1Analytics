#!/usr/bin/env python
"""Re-test the win market before qualifying, season by season, with error bars.

Usage:
    python scripts/backtest_win_market.py --from-api [--api-url http://localhost:8001]
    python scripts/backtest_win_market.py <results.jsonl> [--quali quali.jsonl]

The pre-quali window publishes podium and points but not the win
(``MARKETS_BY_WINDOW`` in ``app/models/schemas.py``): on two held-out seasons
its win skill came out at -19.6% and +13.5%, which is no skill at all. That was
measured before the model had qualifying-gap and practice long-run features.
This script measures it again on the model as it is now.

For each season S in ``--seasons``, the linear model is fitted on the seasons
before S only. Its temperature is chosen inside the fold, on the fold's last
training season, with weights fitted on the seasons before that one. Then every
race of S is scored on the win market. Both windows are scored the same way, so
the pre-quali result can be read against the post-quali one:

* ``pre_quali``: no grid. This weekend's practice is allowed, its qualifying
  is not, exactly as ``build_snapshot`` cuts them for the window.
* ``post_quali``: the grid as it started.

Two scores per race, each as skill over a uniform guess (1/field):

* Brier on the win market, the measure the track record and
  ``backtest_sprints.py`` use.
* Log loss on the real winner, the measure that punishes a confident miss.

The verdict this prints is the rule for putting the win back on the pre-quali
window: the pooled Brier interval sits wholly above zero and no season comes
out at or below zero. Nothing here writes or changes what is served.
"""

import argparse
import json
import logging
import os
import sys
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from app.services.model import feature_names  # noqa: E402
from app.training import backtest, dataset, plackett_luce  # noqa: E402
from app.training.plackett_luce import RaceObservation  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backtest_win_market")

#: Temperatures tried inside each fold. 1.0 is the raw Plackett-Luce fit;
#: above 1 flattens the chances, below 1 sharpens them.
TEMPERATURES = (0.6, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)
WINDOWS = ("pre_quali", "post_quali")
#: One race's scores: model Brier, uniform Brier, model log loss, uniform log loss.
RaceScore = Tuple[float, float, float, float]


def win_probabilities(weights: np.ndarray, race: RaceObservation,
                      temperature: float = 1.0) -> np.ndarray:
    """The Plackett-Luce chance of each driver being first: a softmax."""
    utility = race.features @ weights / temperature
    utility = utility - utility.max()
    expo = np.exp(utility)
    return expo / expo.sum()


def score_race(p: np.ndarray, race: RaceObservation) -> RaceScore:
    """Win-market Brier and winner log loss, the model's and a uniform guess's."""
    n = len(p)
    winner = race.order[0]
    outcome = np.zeros(n)
    outcome[winner] = 1.0
    brier = float(((p - outcome) ** 2).sum())
    uniform_brier = 1.0 - 1.0 / n
    log_loss = -float(np.log(max(p[winner], 1e-12)))
    return brier, uniform_brier, log_loss, float(np.log(n))


def _fit(train: Sequence[RaceObservation], names: Sequence[str]) -> np.ndarray:
    weights = plackett_luce.fit(list(train), names)
    return np.array([weights[name] for name in names])


def choose_temperature(train: Sequence[RaceObservation], names: Sequence[str],
                       temperatures: Sequence[float] = TEMPERATURES) -> float:
    """The temperature with the lowest win Brier on the fold's last training
    season, from weights fitted on the seasons before it. 1.0 when the fold
    is too short to hold a season back."""
    last = max(o.season for o in train)
    inner = [o for o in train if o.season < last]
    check = [o for o in train if o.season == last]
    if not inner or not check:
        return 1.0
    weights = _fit(inner, names)

    def brier(t):
        return sum(score_race(win_probabilities(weights, o, t), o)[0] for o in check)

    return min(temperatures, key=brier)


def win_factory(train: List[RaceObservation], names: Sequence[str]
                ) -> Callable[[RaceObservation], RaceScore]:
    weights = _fit(train, names)
    temperature = choose_temperature(train, names)
    logger.info("  fold before %s: temperature %s",
                max(o.season for o in train) + 1, temperature)
    return lambda race: score_race(win_probabilities(weights, race, temperature), race)


def skill(scores: Sequence[RaceScore]) -> Dict[str, Dict[str, float]]:
    """Brier and log-loss skill over uniform, each with a 95% interval over
    races. Losses are negated so the shared interval reads higher as better."""
    out = {}
    for label, model, uniform in (("brier", 0, 1), ("log_loss", 2, 3)):
        point, low, high = backtest.improvement_interval(
            [-s[model] for s in scores], [-s[uniform] for s in scores])
        out[label] = {"skill": point, "low": low, "high": high}
    return out


def summarise(folds_by_window: Dict[str, List[backtest.FoldResult]]) -> Dict:
    summary: Dict = {}
    for window, folds in folds_by_window.items():
        seasons = []
        for fold in folds:
            scores = fold.scores["win"]
            seasons.append({"season": fold.season, "races": len(scores), **skill(scores)})
        pooled = [s for fold in folds for s in fold.scores["win"]]
        summary[window] = {"seasons": seasons, "pooled": skill(pooled), "races": len(pooled)}
    if "pre_quali" in summary:
        summary["verdict"] = verdict(summary["pre_quali"])
    return summary


def verdict(window: Dict) -> str:
    """Whether the pre-quali win market has earned its place back."""
    pooled = window["pooled"]["brier"]
    negative = [s["season"] for s in window["seasons"] if s["brier"]["skill"] <= 0]
    if pooled["low"] > 0 and not negative:
        return ("publish: pooled Brier skill {:+.1%} [{:+.1%}, {:+.1%}] and above zero "
                "in every season".format(pooled["skill"], pooled["low"], pooled["high"]))
    reasons = []
    if pooled["low"] <= 0:
        reasons.append("pooled interval reaches {:+.1%}".format(pooled["low"]))
    if negative:
        reasons.append("at or below zero in {}".format(", ".join(map(str, negative))))
    return "keep it off: " + "; ".join(reasons)


def _cell(entry: Dict[str, float]) -> str:
    return "{:+6.1%} [{:+6.1%}, {:+6.1%}]".format(entry["skill"], entry["low"], entry["high"])


def report(summary: Dict) -> None:
    for window in WINDOWS:
        if window not in summary:
            continue
        logger.info("\n=== %s win market, skill over uniform, 95%% interval ===", window)
        logger.info("{:<7} {:>5}   {:<26}   {:<26}".format("season", "races", "brier", "log loss"))
        rows = summary[window]["seasons"] + [
            dict(season="all", races=summary[window]["races"], **summary[window]["pooled"])]
        for row in rows:
            logger.info("{:<7} {:>5}   {:<26}   {:<26}".format(
                row["season"], row["races"], _cell(row["brier"]), _cell(row["log_loss"])))
    if "verdict" in summary:
        logger.info("\nverdict for the pre-quali win market: %s", summary["verdict"])


def run_observations(observations_by_window: Dict[str, List[RaceObservation]],
                     seasons: Sequence[int], from_season: int, decay: float) -> Dict:
    """The rolling folds and summary, from observations already built."""
    folds_by_window = {}
    for window, observations in observations_by_window.items():
        folds = backtest.rolling_folds(
            observations, feature_names(with_grid=window == "post_quali"), seasons,
            {"win": win_factory}, decay=decay, from_season=from_season)
        if not folds:
            raise SystemExit("no folds for {}: check --seasons against the corpus".format(window))
        folds_by_window[window] = folds
    return summarise(folds_by_window)


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
    parser.add_argument("--pre-quali-only", action="store_true",
                        help="Skip the post-quali comparison (faster).")
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
    elif args.results:
        results = load_results(args.results)
        quali = load_quali(args.quali)
    else:
        parser.error("pass a results JSONL or --from-api")

    usable = [row for row in results if row.season >= args.from_season]
    windows = ("pre_quali",) if args.pre_quali_only else WINDOWS
    observations = {
        window: dataset.build_observations(
            usable, target_season=max(seasons), with_grid=window == "post_quali",
            decay=args.decay, quali=quali, practice=practice)
        for window in windows
    }
    summary = run_observations(observations, seasons, args.from_season, args.decay)
    report(summary)
    if args.json:
        with open(args.json, "w") as handle:
            json.dump(summary, handle, indent=2)
        logger.info("\nwrote %s", os.path.abspath(args.json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
