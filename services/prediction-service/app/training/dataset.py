"""Turning historical results into training examples.

Every example is built by the *same* ``build_snapshot`` the service calls at
prediction time. That is deliberate and load-bearing: a separate training-time
feature path is the classic way to ship a model that validates beautifully and
is quietly wrong in production, because the two implementations drift and
nothing in the output reveals it.

It also means the point-in-time guarantee is inherited for free. Training
examples for round N are built from rounds < N, so the model is never fitted on
information it will not have when forecasting.
"""

import logging
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.models.schemas import LockWindow
from app.services.features import build_snapshot
from app.services.ingestion_client import (
    GridSlot,
    PracticePace,
    QualiResult,
    RaceResult,
)
from app.services.model import feature_names, feature_vector
from app.training import eras
from app.training.plackett_luce import RaceObservation

logger = logging.getLogger(__name__)

#: A race needs a real field to teach anything about ordering.
MIN_FIELD = 6
#: And at least a few classified finishers to give the ranking any content.
MIN_FINISHERS = 3


def build_observations(
    results: Sequence[RaceResult],
    target_season: int,
    with_grid: bool,
    decay: float = 0.6,
    min_round: int = 2,
    quali: Optional[Sequence[QualiResult]] = None,
    practice: Optional[Sequence[PracticePace]] = None,
) -> List[RaceObservation]:
    """Build one observation per race.

    ``min_round=2`` skips season openers: with no prior rounds in the season the
    features are almost entirely cold-start defaults, so the race contributes
    noise rather than signal about what the features mean.
    """
    by_season: Dict[int, List[RaceResult]] = {}
    for row in results:
        by_season.setdefault(row.season, []).append(row)

    quali_by_season: Dict[int, List[QualiResult]] = {}
    for row in quali or []:
        quali_by_season.setdefault(row.season, []).append(row)

    practice_by_season: Dict[int, List[PracticePace]] = {}
    for row in practice or []:
        practice_by_season.setdefault(row.season, []).append(row)

    observations: List[RaceObservation] = []
    for season in sorted(by_season):
        season_rows = by_season[season]
        # Prior season history is legitimately known and materially improves
        # early-season features.
        history = season_rows + by_season.get(season - 1, [])
        circuits = {row.round: row.circuit for row in season_rows}
        quali_history = quali_by_season.get(season, []) + quali_by_season.get(
            season - 1, []
        )

        for round_number in sorted({row.round for row in season_rows}):
            if round_number < min_round:
                continue
            observation = _observation_for(
                history=history,
                season_rows=season_rows,
                season=season,
                round_number=round_number,
                circuit=circuits.get(round_number, ""),
                with_grid=with_grid,
                target_season=target_season,
                decay=decay,
                quali_history=quali_history,
                practice=practice_by_season.get(season, []),
            )
            if observation is not None:
                observations.append(observation)

    logger.info(
        "built %s observations from %s seasons", len(observations), len(by_season)
    )
    return observations


def _observation_for(
    history: Sequence[RaceResult],
    season_rows: Sequence[RaceResult],
    season: int,
    round_number: int,
    circuit: str,
    with_grid: bool,
    target_season: int,
    decay: float,
    quali_history: Optional[Sequence[QualiResult]] = None,
    practice: Optional[Sequence[PracticePace]] = None,
) -> Optional[RaceObservation]:
    actual = [row for row in season_rows if row.round == round_number]
    if len(actual) < MIN_FIELD:
        return None

    grid: Optional[List[GridSlot]] = None
    if with_grid:
        # Historical grid slots come free with the race results, so a post-quali
        # model can be trained on every race ever run rather than only those
        # whose qualifying session has been separately ingested.
        grid = [
            GridSlot(
                season=season,
                round=round_number,
                driver=row.driver,
                team=row.team,
                position=row.grid_position,
                # Race results carry the real starting grid, penalties already
                # applied — so training examples are always confirmed. This is
                # exactly the fact serving must match, and the reason a
                # provisional grid at serving time is a train/serve mismatch
                # rather than a rounding error.
                grid_position=row.grid_position,
            )
            for row in actual
            if row.grid_position > 0
        ]
        if len(grid) < MIN_FIELD:
            return None

    snapshot = build_snapshot(
        history,
        season=season,
        target_round=round_number,
        window=LockWindow.POST_QUALI if with_grid else LockWindow.PRE_QUALI,
        circuit=circuit,
        grid=grid,
        quali_history=quali_history,
        practice=practice,
    )
    if not snapshot.drivers or snapshot.as_of_round == 0:
        return None

    index_of = {driver.driver: index for index, driver in enumerate(snapshot.drivers)}
    finishers = sorted(
        (row for row in actual if row.classified and row.driver in index_of),
        key=lambda row: row.position,
    )
    if len(finishers) < MIN_FINISHERS:
        return None

    bucket = eras.era_bucket(season, target_season)
    matrix = np.vstack(
        [
            feature_vector(driver, with_grid=with_grid, era_bucket=bucket)
            for driver in snapshot.drivers
        ]
    )

    return RaceObservation(
        features=matrix,
        order=[index_of[row.driver] for row in finishers],
        season=season,
        round_number=round_number,
        weight=eras.sample_weight(season, target_season, decay=decay),
    )


def standardise(
    observations: Sequence[RaceObservation], names: Sequence[str]
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Compute and apply feature scaling, returning the parameters to store.

    Scaling is computed once over the whole training set and saved with the
    weights. At prediction time the stored values are reused rather than
    recomputed from the current field — otherwise a driver's strength would
    depend on who else happened to enter that race.

    Grid-era columns are exempt: they are zero for every race outside their own
    era by construction, so centring them would make "not this era" a non-zero
    signal and destroy the interaction.
    """
    stacked = np.vstack([observation.features for observation in observations])
    means: Dict[str, float] = {}
    stds: Dict[str, float] = {}

    for index, name in enumerate(names):
        column = stacked[:, index]
        if name.startswith("grid_"):
            # Scale only, never centre.
            nonzero = column[column != 0]
            spread = float(nonzero.std()) if nonzero.size else 1.0
            means[name] = 0.0
            stds[name] = spread if spread > 1e-9 else 1.0
        else:
            means[name] = float(column.mean())
            spread = float(column.std())
            stds[name] = spread if spread > 1e-9 else 1.0

    mean_vector = np.array([means[name] for name in names])
    std_vector = np.array([stds[name] for name in names])
    for observation in observations:
        observation.features = (observation.features - mean_vector) / std_vector

    return means, stds


def split_by_season(
    observations: Sequence[RaceObservation], test_seasons: Sequence[int]
) -> Tuple[List[RaceObservation], List[RaceObservation]]:
    """Temporal split. Never random — a random split leaks the future.

    Shuffling races would put round 12 of a season in training and round 8 in
    test, letting the model learn from a car upgrade before predicting the race
    that preceded it. The reported score would be meaningless.
    """
    test_set = set(test_seasons)
    train = [o for o in observations if o.season not in test_set]
    test = [o for o in observations if o.season in test_set]
    return train, test
