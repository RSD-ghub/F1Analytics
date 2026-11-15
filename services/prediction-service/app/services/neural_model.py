"""The neural race model: a small network scores the field, simulation does the rest.

A drop-in ``RaceModel``. Only the strength function changes — a one-hidden-layer
network over the same feature vector instead of a straight weighted sum (see
``app/training/neural.py`` for the network and why it is shaped that way).
Sampling, retirements and the market summaries are inherited, so the network's
races are simulated exactly the way the linear model's are and the two can be
compared number for number.

**Grid-only.** The network is fitted on races with a starting grid and serves
only forecasts made on one: the race simulation on the confirmed grid, and the
final-grid lock when it has been promoted. There is deliberately no grid-blind
version — that would be a second network fitted on a few hundred races, to
publish a window whose win market was already shown to be no better than
guessing.

**Promotion is in the artifact.** ``scripts/train_neural.py`` refits the linear
model on the same corpus and scores both on held-out seasons. Only if the
network wins does it write ``promoted: true``, and only then does the final-grid
lock use it. Until then the simulation is still shown, labelled with that
comparison, and the permanent record stays on the measured model.
"""

import json
import logging
import os
from typing import Any, Dict, Optional, Sequence

import numpy as np

from app.models.schemas import DriverFeatures
from app.services.model import (
    NEURAL_WEIGHTS_PATH,
    SERVING_ERA_BUCKET,
    ModelWeightsMissing,
    RaceModel,
    feature_names,
    feature_vector,
)
from app.training.neural import NetworkParameters, forward

logger = logging.getLogger(__name__)

#: Bump on any change that alters output for the same input, as for the linear
#: model: the track record is grouped by version.
#:
#: v2 — retirements cluster by race and by team (gamma hazard multipliers
#:      fitted to the record's moments); was independent per car.
#: v1 — hidden layer on the frozen Plackett-Luce weights, 10,000 runs.
NEURAL_MODEL_VERSION = "race-neural-v2"

#: How many races are simulated on the confirmed grid. Ten thousand puts the
#: Monte Carlo error on a 30% win chance under half a point.
SIMULATION_RUNS = 10000


class NeuralRaceModel(RaceModel):
    def __init__(
        self,
        params: NetworkParameters,
        means: Dict[str, float],
        stds: Dict[str, float],
        noise_scale: float = 1.0,
        runs: int = SIMULATION_RUNS,
        version: str = NEURAL_MODEL_VERSION,
        promoted: bool = False,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            means=means, stds=stds, noise_scale=noise_scale, runs=runs,
            version=version, pre_quali_noise_scale=noise_scale,
        )
        names = feature_names(with_grid=True)
        if params.inputs != len(names):
            raise ValueError(
                "network expects {} inputs but the feature vector has {}; the "
                "artifact was fitted on a different feature set".format(
                    params.inputs, len(names)
                )
            )
        self.params = params
        self.promoted = promoted
        self.metrics = metrics or {}
        self._names = names

    @classmethod
    def load(
        cls, path: str = NEURAL_WEIGHTS_PATH, runs: int = SIMULATION_RUNS
    ) -> "NeuralRaceModel":
        resolved = os.path.abspath(path)
        if not os.path.exists(resolved):
            raise ModelWeightsMissing(
                "no neural weights at {}; run scripts/train_neural.py".format(resolved)
            )
        with open(resolved) as handle:
            artifact = json.load(handle)
        logger.info(
            "loaded neural model %s fitted %s (promoted=%s)",
            artifact.get("version"), artifact.get("fitted_at"),
            artifact.get("promoted"),
        )
        model = cls(
            params=NetworkParameters.from_dict(artifact["network"]),
            means=artifact["feature_means"],
            stds=artifact["feature_stds"],
            noise_scale=artifact.get("noise_scale", 1.0),
            runs=runs,
            version=artifact.get("version", NEURAL_MODEL_VERSION),
            promoted=bool(artifact.get("promoted", False)),
            metrics=artifact.get("metrics", {}),
        )
        clustering = artifact.get("retirement_clustering") or {}
        model.retirement_race_k = clustering.get("race_k")
        model.retirement_team_k = clustering.get("team_k")
        return model

    @property
    def uses_grid(self) -> bool:
        return True

    def parameters(self) -> Dict[str, float]:
        return {
            "hidden_units": float(self.params.hidden),
            "noise_scale": self.noise_scale,
            "runs": float(self.runs),
        }

    def strengths(self, drivers: Sequence[DriverFeatures]) -> np.ndarray:
        if not drivers:
            return np.zeros(0)
        # A driver missing from the grid is modelled from the back rather than
        # from slot zero, which the network would read as better than pole.
        back = len(drivers)
        matrix = np.vstack([
            self.standardise(self._names, feature_vector(
                driver if driver.grid_position > 0
                else driver.model_copy(update={"grid_position": back}),
                with_grid=True, era_bucket=SERVING_ERA_BUCKET,
            ))
            for driver in drivers
        ])
        strengths, _ = forward(self.params, matrix)
        return strengths

    def strength(self, driver: DriverFeatures) -> float:
        return float(self.strengths([driver])[0])
