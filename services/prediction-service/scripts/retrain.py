#!/usr/bin/env python
"""Continuous retraining with a champion/challenger gate.

Usage:
    python scripts/retrain.py <results.jsonl> [--apply]

Run this after new races have been ingested and reconciled. It trains a
challenger on everything available, scores it against the incumbent on races
*neither* has seen, and promotes only on a real margin. Without ``--apply`` it
reports the decision and changes nothing.

**This is a loop toward "provably better", not toward a target number.** Race
outcomes carry irreducible randomness — pole position converts to a win 53% of
the time, and in 2025 even always backing the season's most frequent winner
would have been right 33% of the time. A loop pushed toward a fixed accuracy
figure would have to manufacture confidence the races do not contain, which
means breaking calibration: the one property the published track record exists
to defend. So the objective is to beat the current model on fresh evidence, for
as long as that remains possible, and to say so plainly when it stops.

Promotion archives the outgoing model rather than overwriting it, so every
prediction ever published can still be reproduced from the weights that made it.
"""

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.model import MODEL_VERSION, WEIGHTS_PATH, feature_names  # noqa: E402
from app.training import dataset, promotion  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from train_model import load_results, train_once  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("retrain")

ARTIFACT = os.path.abspath(WEIGHTS_PATH)
ARCHIVE_DIR = os.path.join(os.path.dirname(ARTIFACT), "model_archive")
HISTORY = os.path.join(os.path.dirname(ARTIFACT), "promotion_history.json")


def load_champion() -> Optional[Dict]:
    if not os.path.exists(ARTIFACT):
        return None
    with open(ARTIFACT) as handle:
        return json.load(handle)


def append_history(decision: Dict) -> None:
    """Every decision is recorded, including the rejections.

    A run of rejections is the signal that the current feature set has stopped
    improving — useful, and invisible if only promotions are logged.
    """
    entries: List[Dict] = []
    if os.path.exists(HISTORY):
        with open(HISTORY) as handle:
            try:
                entries = json.load(handle)
            except ValueError:
                entries = []
    entries.append(decision)
    with open(HISTORY, "w") as handle:
        json.dump(entries, handle, indent=2)


def archive(champion: Dict) -> str:
    """Keep the outgoing model so its published predictions stay reproducible."""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(
        ARCHIVE_DIR, "{}-{}.json".format(champion.get("version", "unknown"), stamp)
    )
    shutil.copy2(ARTIFACT, path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results")
    parser.add_argument("--from-season", type=int, default=2010)
    parser.add_argument("--validation-seasons", default="2018,2019,2021,2022,2023")
    parser.add_argument("--target-season", type=int, default=2026)
    parser.add_argument("--decay", type=float, default=0.6)
    parser.add_argument("--min-races", type=int, default=promotion.MIN_EVALUATION_RACES)
    parser.add_argument("--apply", action="store_true",
                        help="Promote if the gate passes. Otherwise report only.")
    args = parser.parse_args()

    champion = load_champion()
    if champion is None:
        logger.error(
            "no champion at %s — run scripts/train_model.py --write first. "
            "Retraining compares against an incumbent; there is nothing to "
            "compare against yet.", ARTIFACT
        )
        return 1

    results = load_results(args.results)
    latest_season = max(row.season for row in results)
    logger.info("data through %s (%s rows)", latest_season, len(results))

    champion_trained_through = max(champion.get("training_seasons") or [0])
    champion_evaluated_through = max(
        champion.get("promoted_on_seasons") or champion.get("test_seasons") or [0]
    )
    logger.info(
        "champion %s: trained through %s, last evaluated through %s",
        champion.get("version"), champion_trained_through, champion_evaluated_through,
    )

    # Build observations once; both models are scored on exactly the same races.
    observations = dataset.build_observations(
        [r for r in results if r.season >= args.from_season],
        target_season=args.target_season, with_grid=True, decay=args.decay,
    )
    names = feature_names(True)
    dataset.standardise(observations, names)

    evaluation = promotion.unseen_races(
        observations, champion_trained_through, champion_evaluated_through
    )
    logger.info(
        "%s race(s) unseen by the champion, from seasons %s",
        len(evaluation), sorted({o.season for o in evaluation}) or "-",
    )

    if len(evaluation) < args.min_races:
        decision = promotion.compare(
            champion["weights"], champion["weights"], names, evaluation,
            min_races=args.min_races,
        )
        logger.info("\nHOLD: %s", decision.reason)
        append_history(decision.as_dict())
        return 0

    # Train the challenger on everything *except* the evaluation seasons, so the
    # comparison is on races neither model has learned from.
    evaluation_seasons = sorted({o.season for o in evaluation})
    validation_seasons = [int(s) for s in args.validation_seasons.split(",") if s.strip()]
    challenger = train_once(
        results, with_grid=True,
        test_seasons=list(evaluation_seasons) + validation_seasons,
        target_season=args.target_season, decay=args.decay,
        from_season=args.from_season,
    )

    decision = promotion.compare(
        champion["weights"], challenger["weights"], names, evaluation,
        min_races=args.min_races,
    )

    logger.info("\n=== promotion decision ===")
    logger.info("evaluated on      : %s races, seasons %s",
                decision.evaluation_races, decision.evaluated_seasons)
    logger.info("uniform baseline  : %.4f", decision.baseline_score)
    logger.info("champion          : %.4f", decision.champion_score)
    logger.info("challenger        : %.4f", decision.challenger_score)
    logger.info("relative gain     : %+.2f%%", 100 * decision.relative_gain)
    logger.info("decision          : %s", "PROMOTE" if decision.promote else "HOLD")
    logger.info("reason            : %s", decision.reason)

    record = decision.as_dict()
    record["champion_version"] = champion.get("version")
    append_history(record)

    if decision.promote and args.apply:
        archived = archive(champion)
        logger.info("archived outgoing champion to %s", archived)
        _write_promoted(champion, challenger, names, evaluation_seasons, decision)
        logger.info("promoted challenger to %s", ARTIFACT)
    elif decision.promote:
        logger.info("\n(dry run — pass --apply to promote)")

    return 0


def _write_promoted(champion, challenger, names, evaluation_seasons, decision) -> None:
    """Write the new champion, carrying its provenance forward.

    ``promoted_on_seasons`` is what stops the next cycle from re-using these
    races to justify another promotion — the anti-holdout-burn record.
    """
    artifact = dict(champion)
    artifact.update(
        {
            "version": MODEL_VERSION,
            "fitted_at": datetime.now(timezone.utc).isoformat(),
            "training_seasons": challenger["train_seasons"],
            "weights": challenger["weights"],
            "feature_means": challenger["means"],
            "feature_stds": challenger["stds"],
            "promoted_on_seasons": evaluation_seasons,
            "promotion": decision.as_dict(),
            "previous_version": champion.get("version"),
        }
    )
    with open(ARTIFACT, "w") as handle:
        json.dump(artifact, handle, indent=2, sort_keys=True)


if __name__ == "__main__":
    sys.exit(main())
