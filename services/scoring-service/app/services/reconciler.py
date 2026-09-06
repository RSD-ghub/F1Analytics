"""Reconciliation orchestration: results in, scores out.

The sweep is idempotent and safe to run on a schedule. It only scores races that
have actually been ingested with a classified winner — reconciling a race whose
results are half-loaded would bake a wrong score into the public record, and a
score is much harder to explain away later than a delayed one.
"""

import logging
from typing import List, Optional, Sequence, Tuple

from app.models.schemas import Market, PredictionScore, RaceOutcome, TrackRecord
from app.services import track_record as record_builder
from app.services.clients import (
    IngestionClient,
    LockedPrediction,
    PredictionClient,
    UpstreamUnavailable,
)
from app.services.reconciliation import build_outcome, score_prediction
from app.services.storage import ScoringStore

logger = logging.getLogger(__name__)


class Reconciler:
    def __init__(
        self,
        ingestion: IngestionClient,
        predictions: PredictionClient,
        store: ScoringStore,
    ) -> None:
        self._ingestion = ingestion
        self._predictions = predictions
        self._store = store

    async def reconcile_race(
        self, season: int, round_number: int
    ) -> Tuple[Optional[RaceOutcome], List[PredictionScore]]:
        """Score every locked prediction for one race.

        Returns ``(None, [])`` when the race has no usable result yet — that is
        the normal state for an upcoming race, not a failure.
        """
        results = await self._ingestion.race_results(season, round_number)
        if not _has_classified_winner(results):
            logger.info(
                "skipping %s-%s: no classified winner in ingested results",
                season,
                round_number,
            )
            return None, []

        race_name = next((r.race_name for r in results if r.race_name), "")
        outcome = build_outcome(season, round_number, results, race_name)
        await self._store.save_outcome(outcome)

        predictions = await self._predictions.predictions_for(season, round_number)
        scores: List[PredictionScore] = []
        for prediction in predictions:
            if prediction.is_late_lock:
                # A call placed after the lights went out is not a forecast, and
                # scoring it would put a result-shaped thing into the public
                # accuracy record. Refused outright rather than flagged: a
                # flagged entry still lands in the aggregate, and the one number
                # this product sells is "we said it beforehand".
                logger.error(
                    "REFUSING to score %s for %s-%s: locked at %s, race started "
                    "at %s. A prediction made after the race is not a forecast.",
                    prediction.prediction_id, season, round_number,
                    prediction.locked_at, prediction.race_start_utc,
                )
                continue
            if prediction.is_late_lock is None:
                # Not provably late, but not provably in time either. Scored,
                # because refusing every prediction that predates the field
                # would erase the record; logged, because an unverifiable lock
                # is a gap in the guarantee and should be visible.
                logger.warning(
                    "cannot verify lock timing for %s (locked_at=%s, "
                    "race_start_utc=%s); scoring it unverified",
                    prediction.prediction_id, prediction.locked_at,
                    prediction.race_start_utc,
                )
            score = score_prediction(prediction, outcome)
            await self._store.save_score(score)
            scores.append(score)

        logger.info(
            "reconciled %s-%s: %s prediction(s) scored", season, round_number, len(scores)
        )
        return outcome, scores

    async def reconcile_season(self, season: int) -> List[PredictionScore]:
        """Sweep every round that has a locked prediction and a finished race."""
        try:
            predictions = await self._predictions.list_predictions(season)
        except UpstreamUnavailable as exc:
            logger.error("cannot list predictions for %s: %s", season, exc)
            raise

        scored: List[PredictionScore] = []
        for round_number in sorted({p.round for p in predictions}):
            try:
                _, scores = await self.reconcile_race(season, round_number)
                scored.extend(scores)
            except UpstreamUnavailable as exc:
                # One unreachable round must not abandon the rest of the sweep;
                # the next run picks it up because reconciliation is idempotent.
                logger.warning("skipping %s-%s: %s", season, round_number, exc)
        return scored

    async def track_record(
        self,
        season: Optional[int] = None,
        buckets: int = 10,
    ) -> TrackRecord:
        """Assemble the public accuracy record.

        Counts locked-but-unscored predictions so the record cannot be improved
        by simply declining to reconcile the bad ones.
        """
        scores = await self._store.list_scores(season=season)
        pending, refused_late = await self._count_pending(season, scores)
        samples = await self._calibration_samples(season, scores)
        return record_builder.build_track_record(
            scores, samples, pending=pending, refused_late=refused_late,
            buckets=buckets
        )

    async def _count_pending(
        self, season: Optional[int], scores: Sequence[PredictionScore]
    ) -> Tuple[int, int]:
        """``(pending, refused_late)``.

        Split rather than summed: one means "not scored yet", the other means
        "never will be, deliberately". Reporting them as one number would let a
        refused forecast masquerade as a backlog.
        """
        try:
            locked = await self._predictions.list_predictions(season)
        except UpstreamUnavailable:
            logger.warning("cannot count pending predictions; reporting 0")
            return 0, 0
        scored_ids = {score.prediction_id for score in scores}
        unscored = [p for p in locked if p.prediction_id not in scored_ids]
        refused = sum(1 for p in unscored if p.is_late_lock)
        return len(unscored) - refused, refused

    async def _calibration_samples(
        self, season: Optional[int], scores: Sequence[PredictionScore]
    ) -> List[Tuple[Market, str, float, bool]]:
        """Re-pair every per-driver probability with what happened.

        Rebuilt from stored predictions and outcomes rather than kept alongside
        the scores: the calibration curve should reflect the source data, so a
        bug in aggregation cannot quietly propagate into it.
        """
        if not scores:
            return []

        outcomes = {o.key: o for o in await self._store.list_outcomes(season)}
        rows: List[Tuple[str, Market, str, float]] = []

        for score in scores:
            try:
                predictions = await self._predictions.predictions_for(
                    score.season, score.round
                )
            except UpstreamUnavailable:
                continue
            for prediction in predictions:
                if prediction.prediction_id != score.prediction_id:
                    continue
                rows.extend(_probability_rows(prediction))

        return record_builder.calibration_samples_from(rows, outcomes)


def _probability_rows(
    prediction: LockedPrediction,
) -> List[Tuple[str, Market, str, float]]:
    """Flatten a prediction into calibration samples.

    Skips any market the forecast did not publish, and any probability that is
    ``None``. Both checks are needed and neither is redundant: the pre-quali
    window declines the win market entirely, and a ``None`` there is the absence
    of a claim rather than a claim of zero.

    Omitting this was a live 500 — the calibration bucketer computes
    ``int(probability * buckets)`` and ``None * 10`` raises. The scoring path
    already skipped unpublished markets; this second path did not, which is
    exactly the sort of divergence that makes "absent means absent" a rule worth
    enforcing in one place rather than remembering in two.
    """
    key_prefix = "{}-{}".format(prediction.season, prediction.round)
    rows: List[Tuple[str, Market, str, float]] = []
    for row in prediction.driver_probabilities:
        key = "{}|{}".format(key_prefix, row.driver)
        for market, probability in (
            (Market.WIN, row.p_win),
            (Market.PODIUM, row.p_podium),
            (Market.POINTS, row.p_points),
        ):
            if probability is None or not prediction.publishes(market.value):
                continue
            rows.append((key, market, prediction.window, probability))
    return rows


def _has_classified_winner(results: Sequence) -> bool:
    """A race is reconcilable only once someone is classified first.

    Guards against scoring a partially-ingested result set, which would write a
    wrong score into a record that is meant to be permanent.
    """
    return any(row.classified and row.position == 1 for row in results)
