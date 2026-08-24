"""Turning race results into outcomes, and locked forecasts into scores.

Two rules that keep the record honest:

**A driver in the forecast who did not race scores as a miss.** They were given a
probability and it did not happen. Dropping them would let the model quietly
delete its worst calls — the ones where it backed a driver who was withdrawn or
never started.

**A driver who raced but was not forecast is recorded, not ignored.** It does not
enter the score (there is no probability to score), but a forecast covering
fifteen of twenty cars is not comparable to one covering all of them, and the
count is what makes that visible.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Sequence

from app.models.schemas import (
    DriverOutcome,
    Market,
    PredictionScore,
    RaceOutcome,
)
from app.services.clients import LockedPrediction, RaceResultRow
from app.services.scoring import outcome_for, score_market

logger = logging.getLogger(__name__)

PODIUM_POSITIONS = 3
POINTS_POSITIONS = 10


def build_outcome(
    season: int,
    round_number: int,
    results: Sequence[RaceResultRow],
    race_name: str = "",
) -> RaceOutcome:
    """Reduce race results to the binary facts the markets ask about.

    An unclassified driver is ``False`` on all three, which is correct rather
    than merely convenient: they did not win, did not podium, did not score.
    """
    drivers = [
        DriverOutcome(
            driver=row.driver,
            position=row.position,
            classified=row.classified,
            won=row.classified and row.position == 1,
            podium=row.classified and 1 <= row.position <= PODIUM_POSITIONS,
            points=row.classified and 1 <= row.position <= POINTS_POSITIONS,
        )
        for row in results
    ]
    return RaceOutcome(
        season=season,
        round=round_number,
        race_name=race_name,
        reconciled_at=datetime.now(timezone.utc),
        drivers=sorted(drivers, key=lambda d: d.driver),
    )


def score_prediction(
    prediction: LockedPrediction, outcome: RaceOutcome
) -> PredictionScore:
    """Score one locked forecast against what happened."""
    by_driver: Dict[str, DriverOutcome] = {d.driver: d for d in outcome.drivers}

    predicted_names = [p.driver for p in prediction.driver_probabilities]
    predicted_not_raced = sorted(
        name for name in predicted_names if name not in by_driver
    )
    raced_not_predicted = sorted(
        name for name in by_driver if name not in set(predicted_names)
    )

    markets: List = []
    for market in Market:
        # A market the forecast never claimed is not scored at all. It must not
        # be scored as 0.0 either: that would read "we made no claim" as "we are
        # certain this driver cannot win", and every actual winner would then
        # register as a confident miss — making a forecast look far worse than
        # it is for the crime of being honest about what it does not predict.
        if not prediction.publishes(market.value):
            continue

        probabilities: List[float] = []
        outcomes: List[bool] = []
        for row in prediction.driver_probabilities:
            probability = _probability_for(market, row)
            if probability is None:
                continue
            probabilities.append(probability)
            # A predicted driver who never raced did not win, podium or score.
            # Scoring them as a miss is what stops a bad call from vanishing.
            found = by_driver.get(row.driver)
            outcomes.append(outcome_for(market, found) if found else False)
        markets.append(score_market(market, probabilities, outcomes))

    return PredictionScore(
        score_id=str(uuid.uuid4()),
        prediction_id=prediction.prediction_id,
        season=prediction.season,
        round=prediction.round,
        window=prediction.window,
        model_version=prediction.model_version,
        scored_at=datetime.now(timezone.utc),
        data_complete=prediction.data_complete,
        markets=markets,
        predicted_not_raced=predicted_not_raced,
        raced_not_predicted=raced_not_predicted,
    )


def _probability_for(market: Market, row):
    """The stated probability, or ``None`` where no claim was made."""
    if market is Market.WIN:
        return row.p_win
    if market is Market.PODIUM:
        return row.p_podium
    return row.p_points


def top_pick_was_correct(prediction: LockedPrediction, outcome: RaceOutcome) -> bool:
    """Did the highest-probability driver win?

    A weak measure — it throws away everything the forecast said about the other
    nineteen cars — but readers expect it, so it is reported and never used to
    tune anything.
    """
    ranked = [p for p in prediction.driver_probabilities if p.p_win is not None]
    if not ranked:
        return False
    top = max(ranked, key=lambda p: p.p_win)
    winner = next((d for d in outcome.drivers if d.won), None)
    return winner is not None and winner.driver == top.driver
