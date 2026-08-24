"""Proper scoring rules and calibration.

**Why Brier and log score rather than accuracy.** Both are *proper* scoring
rules: they are minimised only by reporting your true beliefs. Under an accuracy
measure ("was the top pick right?"), a model is rewarded for saying 99% when it
believes 51% — overconfidence costs nothing and looks better. Under a proper
rule it is punished. Since the product's promise is honest probabilities, the
metric has to be one that honesty wins.

**Why every score carries a baseline.** A Brier score of 0.043 is not a claim a
reader can evaluate — it depends entirely on how hard the question is. Winning is
rare, so predicting "nobody wins" for every driver already scores about 0.045 on
a 20-car grid. Skill score answers the question that actually matters: did the
model beat the naive alternative, and by how much?

**Why the two are different questions.** Brier measures how close probabilities
were to reality. Calibration measures whether stated confidence matches observed
frequency. A model can be accurate and overconfident, or perfectly calibrated and
useless. Reporting only one hides half the truth.
"""

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.models.schemas import (
    CalibrationBucket,
    CalibrationCurve,
    DriverOutcome,
    Market,
    MarketScore,
)

#: Log score is unbounded as p → 0. Real forecasts do emit 0.000 for a
#: backmarker, and an infinite penalty for one surprise would swamp every other
#: prediction ever made. Clipping bounds a single miss at ~13.8.
LOG_EPSILON = 1e-6

#: How many drivers each market expects to be true in one race.
MARKET_WINNERS = {Market.WIN: 1, Market.PODIUM: 3, Market.POINTS: 10}


def brier_score(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float:
    """Mean squared error between stated probability and what happened.

    0.0 is perfect, 1.0 is maximally wrong on every driver. Lower is better.
    """
    if not probabilities:
        return 0.0
    return sum(
        (p - (1.0 if o else 0.0)) ** 2 for p, o in zip(probabilities, outcomes)
    ) / len(probabilities)


def log_score(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float:
    """Mean negative log-likelihood. Lower is better.

    Punishes confident mistakes far harder than Brier does — saying 1% about
    something that happens is very expensive. That severity is the point: it is
    what makes hedging irrational when you genuinely are uncertain.
    """
    if not probabilities:
        return 0.0
    total = 0.0
    for probability, outcome in zip(probabilities, outcomes):
        clipped = min(max(probability, LOG_EPSILON), 1.0 - LOG_EPSILON)
        total -= math.log(clipped if outcome else 1.0 - clipped)
    return total / len(probabilities)


def baseline_probability(market: Market, field_size: int) -> float:
    """The naive forecast: every driver equally likely.

    This is the bar a model must clear to have demonstrated anything. It needs no
    data, no features and no model — so a skill score at or below zero means the
    whole pipeline added nothing.
    """
    if field_size <= 0:
        return 0.0
    return min(MARKET_WINNERS[market] / field_size, 1.0)


def skill_score(model_brier: float, baseline_brier: float) -> float:
    """Fraction of the baseline's error the model removed.

    1.0 is perfect, 0.0 matches the baseline, negative is worse than guessing.
    A zero-error baseline (a degenerate case) yields 0.0 rather than dividing by
    zero — claiming infinite skill against a perfect baseline would be absurd.
    """
    if baseline_brier <= 0:
        return 0.0
    return 1.0 - (model_brier / baseline_brier)


def score_market(
    market: Market,
    probabilities: Sequence[float],
    outcomes: Sequence[bool],
) -> MarketScore:
    """Score one market for one race."""
    field_size = len(probabilities)
    if field_size == 0:
        return MarketScore(market=market)

    baseline = baseline_probability(market, field_size)
    model_brier = brier_score(probabilities, outcomes)
    baseline_brier = brier_score([baseline] * field_size, outcomes)

    return MarketScore(
        market=market,
        brier=model_brier,
        baseline_brier=baseline_brier,
        skill_vs_baseline=skill_score(model_brier, baseline_brier),
        log_score=log_score(probabilities, outcomes),
        drivers_scored=field_size,
    )


def outcome_for(market: Market, outcome: DriverOutcome) -> bool:
    if market is Market.WIN:
        return outcome.won
    if market is Market.PODIUM:
        return outcome.podium
    return outcome.points


# ── Calibration ──────────────────────────────────────────────────────────────


def build_calibration(
    samples: Iterable[Tuple[float, bool]],
    market: Market,
    buckets: int = 10,
    window: Optional[str] = None,
    model_version: Optional[str] = None,
) -> CalibrationCurve:
    """Bucket predictions by stated probability and compare to what happened.

    Empty buckets are dropped rather than reported as 0% observed — a band with
    no predictions in it is an absence of evidence, and plotting it as a data
    point at zero would draw a curve that looks badly miscalibrated where
    nothing was ever claimed.
    """
    edges = [index / buckets for index in range(buckets + 1)]
    collected: Dict[int, List[Tuple[float, bool]]] = {}
    total = 0

    for probability, happened in samples:
        index = min(int(probability * buckets), buckets - 1)
        collected.setdefault(index, []).append((probability, happened))
        total += 1

    rows: List[CalibrationBucket] = []
    weighted_error = 0.0

    for index in sorted(collected):
        entries = collected[index]
        predicted_mean = sum(p for p, _ in entries) / len(entries)
        observed_rate = sum(1 for _, o in entries if o) / len(entries)
        rows.append(
            CalibrationBucket(
                lower=edges[index],
                upper=edges[index + 1],
                count=len(entries),
                predicted_mean=predicted_mean,
                observed_rate=observed_rate,
            )
        )
        weighted_error += len(entries) * abs(predicted_mean - observed_rate)

    return CalibrationCurve(
        market=market,
        window=window,
        model_version=model_version,
        samples=total,
        buckets=rows,
        expected_calibration_error=(weighted_error / total) if total else 0.0,
    )
