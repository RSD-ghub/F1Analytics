"""Aggregating individual scores into the public accuracy record.

The aggregation choices here are where a track record gets quietly flattered, so
each one is deliberate:

* **Windows never averaged together.** Pre-quali and post-quali answer different
  questions. A combined number would hide the comparison and would drift as the
  mix of window types changed.
* **Model versions reported, not merged.** If two versions are in the data, the
  reader is told. Averaging a retired model's record into the current one's
  restates history.
* **Pending predictions counted.** A record showing only what has been scored can
  be improved by never reconciling the embarrassing ones.
* **Skill scores averaged per prediction, not recomputed from pooled Brier.**
  Pooling would weight races with larger fields more heavily for no defensible
  reason.
"""

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.models.schemas import (
    CalibrationCurve,
    Market,
    PredictionScore,
    RaceOutcome,
    TrackRecord,
    WindowRecord,
)
from app.services.scoring import build_calibration


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def build_window_record(
    window: str,
    scores: Sequence[PredictionScore],
    hit_rates: Optional[Sequence[bool]] = None,
) -> WindowRecord:
    brier: Dict[str, float] = {}
    skill: Dict[str, float] = {}

    for market in Market:
        market_scores = [
            score.market(market) for score in scores if score.market(market)
        ]
        usable = [m for m in market_scores if m and m.drivers_scored > 0]
        if not usable:
            continue
        brier[market.value] = sum(m.brier for m in usable) / len(usable)
        skill[market.value] = sum(m.skill_vs_baseline for m in usable) / len(usable)

    log_scores = [
        score.market(Market.WIN).log_score
        for score in scores
        if score.market(Market.WIN) and score.market(Market.WIN).log_score is not None
    ]

    return WindowRecord(
        window=window,
        predictions_scored=len(scores),
        brier_by_market=brier,
        skill_by_market=skill,
        mean_log_score=_mean(log_scores),
        top_pick_hit_rate=(
            sum(1 for hit in hit_rates if hit) / len(hit_rates)
            if hit_rates
            else None
        ),
    )


def build_track_record(
    scores: Sequence[PredictionScore],
    calibration_samples: Iterable[Tuple[Market, str, float, bool]],
    pending: int = 0,
    refused_late: int = 0,
    buckets: int = 10,
) -> TrackRecord:
    """Assemble the public record from scored predictions."""
    by_window: Dict[str, List[PredictionScore]] = {}
    for score in scores:
        by_window.setdefault(score.window, []).append(score)

    samples = list(calibration_samples)
    curves: List[CalibrationCurve] = []
    for market in Market:
        for window in sorted(by_window):
            market_samples = [
                (probability, happened)
                for sample_market, sample_window, probability, happened in samples
                if sample_market is market and sample_window == window
            ]
            if market_samples:
                curves.append(
                    build_calibration(
                        market_samples, market=market, buckets=buckets, window=window
                    )
                )

    return TrackRecord(
        generated_at=datetime.now(timezone.utc),
        seasons=sorted({score.season for score in scores}),
        model_versions=sorted({score.model_version for score in scores if score.model_version}),
        predictions_scored=len(scores),
        predictions_pending=pending,
        predictions_refused_late=refused_late,
        predictions_incomplete_data=sum(1 for s in scores if not s.data_complete),
        windows=[
            build_window_record(window, by_window[window])
            for window in sorted(by_window)
        ],
        calibration=curves,
    )


def calibration_samples_from(
    prediction_probabilities: Iterable[Tuple[str, Market, str, float]],
    outcomes: Dict[str, RaceOutcome],
) -> List[Tuple[Market, str, float, bool]]:
    """Flatten per-driver probabilities into (market, window, p, happened) rows.

    Keyed on "{season}-{round}|{driver}" so a driver who appears in a prediction
    but not in the results resolves to ``False`` rather than being dropped.
    """
    from app.services.scoring import outcome_for

    rows: List[Tuple[Market, str, float, bool]] = []
    for key, market, window, probability in prediction_probabilities:
        race_key, _, driver = key.partition("|")
        outcome = outcomes.get(race_key)
        happened = False
        if outcome is not None:
            found = next((d for d in outcome.drivers if d.driver == driver), None)
            happened = outcome_for(market, found) if found else False
        rows.append((market, window, probability, happened))
    return rows
