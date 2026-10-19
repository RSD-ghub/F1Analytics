#!/usr/bin/env python
"""Fit the neural race model, race it against the linear one, write the artifact.

Usage:
    python scripts/train_neural.py --from-api [--api-url http://localhost:8001] [--write]
    python scripts/train_neural.py <results.jsonl> [--quali quali.jsonl] [--write]

Same corpus, same features, same splits as ``train_model.py``:

* **train** — every season before the held-out ones; the hidden layer is fitted here.
* **validation** (2022-2023) — picks the network's width and penalty.
* **test** (2024-2025) — scored once, at the end, for both models.

**Built on the Plackett-Luce model.** The network does not start from scratch.
It takes the fitted model's feature scaling, its weights and its calibrated
temperature from ``model_weights.json`` unchanged, and fits only a hidden layer
that corrects its strengths. The first version fitted everything afresh on 152
races and lost to the linear model on the test seasons by a wide margin (5.2%
improvement over uniform against 9.9%): the data cannot support re-learning
what the linear fit already knows. The fitted model alone is also one of the
candidates, so if no hidden layer helps on validation the network *is* that
model and the simulation is exactly as good as the published forecast.

**Promotion.** The network places the final-grid forecast only if, on the test
seasons, it beats the linear model's log-likelihood by ``MIN_RELATIVE_GAIN`` of
the linear model's edge over uniform — the same margin the champion/challenger
gate uses — and is no worse on the Brier score of the published markets. Either
way the artifact is written with ``--write``; ``promoted`` records the verdict,
and the race simulation shows the network's numbers either way, next to the
linear model's.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Sequence

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from app.models.schemas import LockWindow  # noqa: E402
from app.services.features import build_snapshot  # noqa: E402
from app.services.ingestion_client import GridSlot  # noqa: E402
from app.services.model import (  # noqa: E402
    NEURAL_WEIGHTS_PATH,
    WEIGHTS_PATH,
    RaceModel,
    feature_names,
)
from app.services.neural_model import NEURAL_MODEL_VERSION, NeuralRaceModel  # noqa: E402
from app.services.predictor import derive_seed  # noqa: E402
from app.training import calibrate, dataset, neural, plackett_luce, retirements  # noqa: E402
from app.training.promotion import MIN_RELATIVE_GAIN  # noqa: E402
from train_model import (  # noqa: E402
    TRAINING_FROM_SEASON,
    _load_practice,
    load_quali,
    load_results,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("train_neural")


def calibration_races(results, seasons: Sequence[int], quali=None, practice=None,
                      race_pace_gaps=None) -> List:
    """(drivers, actual positions, seed) for every race in ``seasons``.

    Built through ``build_snapshot`` on the race's real grid, as serving is on a
    confirmed one — the same construction ``train_model.py`` calibrates on.
    """
    races = []
    for season in seasons:
        rows = [r for r in results if r.season in (season, season - 1)]
        circuits = {r.round: r.circuit for r in rows if r.season == season}
        for round_number in sorted({r.round for r in rows if r.season == season}):
            actual = [r for r in rows if r.season == season and r.round == round_number]
            grid = [
                GridSlot(season=season, round=round_number, driver=r.driver,
                         team=r.team, position=r.grid_position,
                         grid_position=r.grid_position)
                for r in actual if r.grid_position > 0
            ]
            if not grid:
                continue
            snapshot = build_snapshot(
                rows, season, round_number, LockWindow.FINAL_GRID,
                circuit=circuits.get(round_number, ""), grid=grid,
                quali_history=[q for q in quali or []
                               if q.season in (season, season - 1)],
                practice=[p for p in practice or [] if p.season == season],
                race_pace_gaps=race_pace_gaps,
            )
            if not snapshot.drivers or snapshot.as_of_round == 0:
                continue
            by_driver = {r.driver: r for r in actual}
            positions = [
                by_driver[d.driver].position
                if d.driver in by_driver and by_driver[d.driver].classified
                else 999
                for d in snapshot.drivers
            ]
            races.append((snapshot.drivers, positions,
                          derive_seed(season, round_number, LockWindow.FINAL_GRID)))
    return races


def head_to_head(
    linear_ll: float, network_ll: float, uniform_ll: float,
    linear_brier: float, network_brier: float,
) -> Dict:
    """The promotion verdict, with the numbers it was reached on."""
    edge = linear_ll - uniform_ll
    gain = (network_ll - linear_ll) / abs(edge) if edge else 0.0
    promote = bool(gain >= MIN_RELATIVE_GAIN and network_brier <= linear_brier)
    if promote:
        reason = "beats the linear model by {:.1%} of its edge over uniform".format(gain)
    elif gain < MIN_RELATIVE_GAIN:
        reason = ("log-likelihood gain of {:.1%} of the linear model's edge is "
                  "under the {:.0%} margin".format(gain, MIN_RELATIVE_GAIN))
    else:
        reason = "ranks better but scores a worse Brier on the published markets"
    return {"promoted": promote, "relative_gain": float(gain), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="?", default=None)
    parser.add_argument("--from-api", action="store_true")
    parser.add_argument("--api-url", default="http://localhost:8001")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--quali", default=None)
    parser.add_argument("--test-seasons", default="2024,2025")
    parser.add_argument("--validation-seasons", default="2022,2023")
    parser.add_argument("--target-season", type=int, default=2026)
    parser.add_argument("--decay", type=float, default=0.6)
    parser.add_argument("--from-season", type=int, default=TRAINING_FROM_SEASON)
    parser.add_argument("--output", default=NEURAL_WEIGHTS_PATH)
    parser.add_argument("--champion", default=WEIGHTS_PATH,
                        help="The Plackett-Luce artifact whose parameters the network builds on.")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    test_seasons = [int(s) for s in args.test_seasons.split(",")]
    validation_seasons = [int(s) for s in args.validation_seasons.split(",") if s.strip()]

    practice = []
    race_pace_gaps = {}
    if args.from_api:
        import asyncio
        from app.services.ingestion_client import IngestionClient
        from app.training import corpus

        client = IngestionClient(args.api_url)
        results, quali = asyncio.run(corpus.load(
            client, args.from_season, max(test_seasons),
            require_complete=not args.allow_incomplete,
        ))
        practice = asyncio.run(_load_practice(client, args.from_season, max(test_seasons)))
        race_pace_gaps = asyncio.run(corpus.load_race_pace(client, results))
    elif args.results:
        results = load_results(args.results)
        quali = load_quali(args.quali)
    else:
        parser.error("pass a results JSONL or --from-api")

    usable = [row for row in results if row.season >= args.from_season]
    observations = dataset.build_observations(
        usable, target_season=args.target_season, with_grid=True,
        decay=args.decay, quali=quali, practice=practice,
        race_pace_gaps=race_pace_gaps,
    )
    names = feature_names(with_grid=True)

    # The Plackett-Luce model's own fitted parameters: its scaling, its
    # weights and its calibrated temperature. The network keeps all three and
    # learns only a correction on top, so it starts as the model that is
    # already measured to work and has to earn any change it makes.
    champion = RaceModel.load(args.champion)
    means = {name: champion.means.get(name, 0.0) for name in names}
    stds = {name: champion.stds.get(name, 1.0) or 1.0 for name in names}
    mean_vector = np.array([means[name] for name in names])
    std_vector = np.array([stds[name] for name in names])
    for observation in observations:
        observation.features = (observation.features - mean_vector) / std_vector
    linear_vector = np.array([champion.weights.get(name, 0.0) for name in names])
    temperature = champion.noise_scale

    rest, test = dataset.split_by_season(observations, test_seasons)
    train, validation = dataset.split_by_season(rest, validation_seasons)
    if not train or not validation or not test:
        raise SystemExit("empty train, validation or test split; check the seasons")
    logger.info("races: train %s, validation %s, test %s",
                len(train), len(validation), len(test))
    logger.info("linear term and temperature (%.2f) from %s", temperature, champion.version)

    params, chosen, sweep = neural.select(train, validation, fixed_linear=linear_vector)
    logger.info("\nnetwork search (validation log-likelihood; hidden 0 is %s alone):",
                champion.version)
    for row in sweep:
        marker = "  <- chosen" if (row["hidden"], row["l2_hidden"]) == (
            chosen["hidden"], chosen["l2_hidden"]) else ""
        logger.info("  hidden %2d  l2 %-5s  %.3f%s", row["hidden"], row["l2_hidden"],
                    row["validation_ll"], marker)

    uniform = plackett_luce.uniform_baseline_log_likelihood(test)
    linear_ll = plackett_luce.mean_log_likelihood(linear_vector, test)
    network_ll = neural.mean_log_likelihood(params, test)

    # Both sampled at the champion's calibrated temperature, then scored on
    # the published markets of the test seasons.
    network_model = NeuralRaceModel(params=params, means=means, stds=stds)
    test_races = calibration_races(results, test_seasons, quali, practice, race_pace_gaps)
    linear_brier = calibrate.evaluate_temperature(champion, test_races, temperature)
    network_brier = calibrate.evaluate_temperature(network_model, test_races, temperature)
    linear_temperature = network_temperature = temperature

    # Retirement clustering, fitted on the training seasons' results and
    # scored on the test seasons against independent retirements.
    train_seasons = {o.season for o in train}
    race_k, team_k, moments = retirements.fit(
        [r for r in results if r.season in train_seasons])
    independent_score = calibrate.retirement_count_score(network_model, test_races)
    network_model.retirement_race_k, network_model.retirement_team_k = race_k, team_k
    clustered_score = calibrate.retirement_count_score(network_model, test_races)
    logger.info(
        "\nretirement clustering (training seasons): x%.2f per-race spread, "
        "x%.2f team-mates together -> race_k %s, team_k %s",
        moments.get("dispersion", 1.0), moments.get("teammate_ratio", 1.0),
        "none" if race_k is None else "%.1f" % race_k,
        "none" if team_k is None else "%.1f" % team_k)
    logger.info("retirement-count log score on test: independent %.3f, clustered %.3f",
                independent_score, clustered_score)

    verdict = head_to_head(linear_ll, network_ll, uniform, linear_brier, network_brier)

    logger.info("\n=== held-out %s (%s races) ===", test_seasons, len(test))
    logger.info("%-28s %12s %12s", "", "linear", "network")
    logger.info("%-28s %12.3f %12.3f", "mean log-likelihood", linear_ll, network_ll)
    logger.info("%-28s %11.1f%% %11.1f%%", "improvement over uniform",
                100 * (linear_ll - uniform) / abs(uniform),
                100 * (network_ll - uniform) / abs(uniform))
    logger.info("%-28s %12.4f %12.4f", "Brier (published markets)",
                linear_brier, network_brier)
    logger.info("%-28s %12.2f %12.2f", "temperature",
                linear_temperature, network_temperature)
    logger.info("\nverdict: %s — %s",
                "PROMOTE" if verdict["promoted"] else "hold", verdict["reason"])

    if not args.write:
        logger.info("\n(dry run — pass --write to save the artifact)")
        return 0

    artifact = {
        "version": NEURAL_MODEL_VERSION,
        "fitted_at": datetime.now(timezone.utc).isoformat(),
        "training_seasons": sorted({o.season for o in train}),
        "validation_seasons": validation_seasons,
        "test_seasons": test_seasons,
        "era_decay": args.decay,
        "target_season": args.target_season,
        "architecture": dict(chosen, inputs=len(names)),
        "architecture_sweep": sweep,
        "network": params.to_dict(),
        "feature_names": names,
        "feature_means": means,
        "feature_stds": stds,
        "noise_scale": temperature,
        "linear_from": champion.version,
        "retirement_clustering": {
            "race_k": race_k, "team_k": team_k, "moments": moments,
            "test_score_independent": independent_score,
            "test_score_clustered": clustered_score,
        },
        "promoted": verdict["promoted"],
        "metrics": {
            "test_races": len(test),
            "uniform_log_likelihood": uniform,
            "linear_log_likelihood": linear_ll,
            "network_log_likelihood": network_ll,
            "linear_brier": linear_brier,
            "network_brier": network_brier,
            "relative_gain": verdict["relative_gain"],
            "verdict": verdict["reason"],
        },
    }
    path = os.path.abspath(args.output)
    with open(path, "w") as handle:
        json.dump(artifact, handle, indent=2, sort_keys=True)
    logger.info("\nwrote %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
