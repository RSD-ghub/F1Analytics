#!/usr/bin/env python
"""Fit the race model and write ``app/model_weights.json``.

Usage:
    python scripts/train_model.py <results.jsonl> [--test-seasons 2024,2025]

Input is one JSON object per line with the fields ingestion-service's
``ResultRow`` produces.

The evaluation is a **temporal** hold-out: train on everything before the test
seasons, score on the test seasons. Never a random split — shuffling races would
let the model learn from round 12 before predicting round 8 of the same season,
and the resulting score would be meaningless.

Reported against a uniform baseline (all weights zero, every driver equally
likely at each step). A fitted model that cannot beat it has learned nothing.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.ingestion_client import QualiResult, RaceResult  # noqa: E402
from app.services.model import MODEL_VERSION, feature_names  # noqa: E402
from app.training import calibrate, dataset, eras, plackett_luce  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("train")

ARTIFACT = os.path.join(os.path.dirname(__file__), "..", "app", "model_weights.json")


def load_results(path: str) -> List[RaceResult]:
    allowed = set(RaceResult.model_fields)
    rows: List[RaceResult] = []
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            rows.append(RaceResult(**{k: v for k, v in raw.items() if k in allowed}))
    return rows


def load_quali(path: Optional[str]) -> List[QualiResult]:
    """Qualifying times, if supplied. Absent is survivable — the features fall
    back to a neutral value and the fit simply has less to work with."""
    if not path or not os.path.exists(path):
        return []
    allowed = set(QualiResult.model_fields)
    rows = []
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if line:
                raw = json.loads(line)
                rows.append(QualiResult(**{k: v for k, v in raw.items() if k in allowed}))
    return rows


async def _load_practice(client, first, last):
    rows = []
    for season in range(first, last + 1):
        rows.extend(await client.season_practice(season))
    logger.info("practice rows: %s", len(rows))
    return rows


def evaluate(
    weights: Dict[str, float],
    names: Sequence[str],
    observations: Sequence[plackett_luce.RaceObservation],
) -> Dict[str, float]:
    vector = np.array([weights[name] for name in names])
    fitted = plackett_luce.mean_log_likelihood(vector, observations)
    baseline = plackett_luce.uniform_baseline_log_likelihood(observations)
    return {
        "mean_log_likelihood": fitted,
        "uniform_baseline": baseline,
        # Fraction of the baseline's surprise removed. Comparable across
        # different field sizes and test-set sizes.
        "improvement": (fitted - baseline) / abs(baseline) if baseline else 0.0,
        "races": len(observations),
    }


def train_once(
    results: Sequence[RaceResult],
    with_grid: bool,
    test_seasons: Sequence[int],
    target_season: int,
    decay: float,
    from_season: int,
    quali: Optional[Sequence[QualiResult]] = None,
    practice: Optional[Sequence] = None,
) -> Dict:
    usable = [row for row in results if row.season >= from_season]
    observations = dataset.build_observations(
        usable, target_season=target_season, with_grid=with_grid, decay=decay,
        quali=quali, practice=practice,
    )
    names = feature_names(with_grid)
    means, stds = dataset.standardise(observations, names)
    train, test = dataset.split_by_season(observations, test_seasons)
    if not train or not test:
        raise SystemExit("empty train or test split; check --test-seasons")

    weights = plackett_luce.fit(train, names)
    return {
        "observations": observations,
        "weights": weights,
        "names": names,
        "means": means,
        "stds": stds,
        "train": evaluate(weights, names, train),
        "test": evaluate(weights, names, test),
        "train_seasons": sorted({o.season for o in train}),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="?", default=None,
                        help="JSONL fallback. Omit to read from ingestion-service.")
    parser.add_argument("--from-api", action="store_true",
                        help="Read the corpus from ingestion-service (preferred).")
    parser.add_argument("--api-url", default="http://localhost:8001")
    parser.add_argument("--allow-incomplete", action="store_true",
                        help="Train despite known gaps. Off by default on purpose.")
    parser.add_argument("--quali", default=None,
                        help="JSONL of qualifying rows with Q1/Q2/Q3 times.")
    parser.add_argument("--test-seasons", default="2024,2025")
    parser.add_argument("--validation-seasons", default="2022,2023",
                        help="Held out from weight fitting; used only to fit temperature.")
    parser.add_argument("--target-season", type=int, default=2026)
    parser.add_argument("--decay", type=float, default=0.6)
    parser.add_argument("--from-season", type=int, default=1990)
    parser.add_argument("--sweep-cutoffs", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    test_seasons = [int(s) for s in args.test_seasons.split(",")]
    validation_seasons = [
        int(s) for s in args.validation_seasons.split(",") if s.strip()
    ]
    practice = []
    if args.from_api:
        import asyncio
        from app.services.ingestion_client import IngestionClient
        from app.training import corpus

        client = IngestionClient(args.api_url)
        results, quali = asyncio.run(
            corpus.load(client, args.from_season, 2025,
                        require_complete=not args.allow_incomplete)
        )
        practice = asyncio.run(_load_practice(client, args.from_season, 2025))
    else:
        results = load_results(args.results)
        quali = load_quali(args.quali)
    logger.info("qualifying rows: %s", len(quali))
    logger.info(
        "loaded %s result rows, seasons %s-%s",
        len(results),
        min(r.season for r in results),
        max(r.season for r in results),
    )

    if args.sweep_cutoffs:
        _sweep(results, test_seasons, args)
        return 0

    report = {}
    for label, with_grid in (("pre_quali", False), ("post_quali", True)):
        outcome = train_once(
            results, with_grid, list(test_seasons) + list(validation_seasons),
            args.target_season, args.decay, args.from_season, quali=quali,
            practice=practice,
        )
        report[label] = outcome
        _print_model(label, outcome)

    temperatures = _fit_temperature(results, report, validation_seasons, args)

    if args.write:
        _write_artifact(report, args, test_seasons, temperatures,
                        validation_seasons)
    else:
        logger.info("\n(dry run — pass --write to save the artifact)")
    return 0


def _fit_temperature(results, report, validation_seasons, args):
    """Calibrate on seasons excluded from both weight fitting and the test set."""
    from app.models.schemas import LockWindow
    from app.services.features import build_snapshot
    from app.services.model import RaceModel
    from app.services.predictor import derive_seed

    if not validation_seasons:
        return 1.0, {}

    model = RaceModel(
        weights=report["post_quali"]["weights"],
        means=report["post_quali"]["means"],
        stds=report["post_quali"]["stds"],
        pre_quali_weights=report["pre_quali"]["weights"],
        noise_scale=1.0,
    )

    def collect(with_grid):
        races = []
        for season in validation_seasons:
            from app.services.ingestion_client import GridSlot
            rows = [r for r in results if r.season in (season, season - 1)]
            circuits = {r.round: r.circuit for r in rows if r.season == season}
            window = LockWindow.POST_QUALI if with_grid else LockWindow.PRE_QUALI
            for round_number in sorted({r.round for r in rows if r.season == season}):
                actual = [r for r in rows
                          if r.season == season and r.round == round_number]
                grid = None
                if with_grid:
                    grid = [GridSlot(season=season, round=round_number,
                                     driver=r.driver, team=r.team,
                                     position=r.grid_position)
                            for r in actual if r.grid_position > 0]
                    if not grid:
                        continue
                snapshot = build_snapshot(
                    rows, season, round_number, window,
                    circuit=circuits.get(round_number, ""), grid=grid,
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
                              derive_seed(season, round_number, window)))
        return races

    out = {}
    for label, with_grid in (("post_quali", True), ("pre_quali", False)):
        races = collect(with_grid)
        logger.info("\n=== temperature: %s (validation %s, %s races) ===",
                    label, validation_seasons, len(races))
        temperature, sweep = calibrate.fit_temperature(model, races)
        for candidate in sorted(sweep):
            marker = "  <- chosen" if candidate == temperature else ""
            logger.info("  %.2f  score %.4f%s", candidate, sweep[candidate], marker)
        out[label] = (temperature, sweep)
    return out


def _print_model(label: str, outcome: Dict) -> None:
    logger.info("\n=== %s ===", label)
    logger.info("train races: %s   test races: %s",
                outcome["train"]["races"], outcome["test"]["races"])
    logger.info("%-22s %12s %12s", "", "train", "held-out")
    logger.info("%-22s %12.3f %12.3f", "mean log-likelihood",
                outcome["train"]["mean_log_likelihood"],
                outcome["test"]["mean_log_likelihood"])
    logger.info("%-22s %12.3f %12.3f", "uniform baseline",
                outcome["train"]["uniform_baseline"],
                outcome["test"]["uniform_baseline"])
    logger.info("%-22s %11.1f%% %11.1f%%", "improvement",
                100 * outcome["train"]["improvement"],
                100 * outcome["test"]["improvement"])
    logger.info("\nfitted weights (standardised — magnitudes comparable):")
    for name, value in sorted(
        outcome["weights"].items(), key=lambda kv: -abs(kv[1])
    ):
        logger.info("  %-22s %+8.4f", name, value)


def _sweep(results, test_seasons, args) -> None:
    """Answer 'how far back should we train?' empirically rather than by taste."""
    logger.info("\n=== training cutoff sweep (held-out %s) ===", test_seasons)
    logger.info("%-8s %-8s %8s %10s %12s", "from", "decay", "races", "test LL", "improvement")
    for from_season in (1990, 2000, 2010, 2014, 2018, 2021):
        for decay in (1.0, args.decay):
            try:
                outcome = train_once(
                    results, False, test_seasons, args.target_season,
                    decay, from_season,
                )
            except SystemExit:
                continue
            logger.info(
                "%-8s %-8.1f %8s %10.3f %11.1f%%",
                from_season, decay, outcome["train"]["races"],
                outcome["test"]["mean_log_likelihood"],
                100 * outcome["test"]["improvement"],
            )


def _check_serving_weights(weights: Dict[str, float]) -> None:
    """Refuse to ship a model whose serving-era grid weight was never fitted.

    A bucket with no training races yields a weight of exactly zero, which makes
    the served model silently ignore grid position — the strongest feature it
    has — while still producing perfectly well-formed probabilities. Nothing
    downstream could detect it, so it is caught here.
    """
    from app.services.model import SERVING_ERA_BUCKET

    key = "grid_{}".format(SERVING_ERA_BUCKET)
    if abs(weights.get(key, 0.0)) < 1e-9:
        raise SystemExit(
            "refusing to write artifact: {} is zero, so the served model would "
            "ignore grid position. The serving era bucket has no training races "
            "- check that --validation-seasons and --test-seasons do not cover "
            "the whole modern era.".format(key)
        )


def _write_artifact(report: Dict, args, test_seasons, temperatures=None,
                    validation_seasons=None) -> None:
    # The post-quali fit is the primary artifact: it is a superset of the
    # pre-quali features, and ``uses_grid`` makes the serving model fall back to
    # the base features automatically when no grid is available.
    primary = report["post_quali"]
    _check_serving_weights(primary["weights"])
    artifact = {
        "version": MODEL_VERSION,
        "fitted_at": datetime.now(timezone.utc).isoformat(),
        "training_seasons": primary["train_seasons"],
        "test_seasons": test_seasons,
        "era_decay": args.decay,
        "target_season": args.target_season,
        # Fitted weights carry their own scale under the Plackett-Luce
        # likelihood, so the sampler must run at 1.0 for them to mean what they
        # were fitted to mean.
        "noise_scale": (temperatures or {}).get("post_quali", (1.0, {}))[0],
        "pre_quali_noise_scale": (temperatures or {}).get("pre_quali", (1.0, {}))[0],
        "validation_seasons": validation_seasons or [],
        "temperature_sweeps": {
            window: {str(k): v for k, v in sweep.items()}
            for window, (_, sweep) in (temperatures or {}).items()
        },
        "weights": primary["weights"],
        "feature_means": primary["means"],
        "feature_stds": primary["stds"],
        "pre_quali_weights": report["pre_quali"]["weights"],
        "metrics": {
            "post_quali": {"train": primary["train"], "test": primary["test"]},
            "pre_quali": {
                "train": report["pre_quali"]["train"],
                "test": report["pre_quali"]["test"],
            },
        },
    }
    path = os.path.abspath(ARTIFACT)
    with open(path, "w") as handle:
        json.dump(artifact, handle, indent=2, sort_keys=True)
    logger.info("\nwrote %s", path)


if __name__ == "__main__":
    sys.exit(main())
