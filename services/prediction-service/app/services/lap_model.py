"""The lap model inside the live forecast: each car's pace from this race so far.

``live_forecast.posterior_pace`` blends a car's pre-race pace with the mean of
its clean laps so far. The linear lap model (``app/training/lap_time_network.py``)
predicts the next lap from more than that: tyre compound and age, fuel, traffic,
laps since a safety car, how fast this stint's tyres are fading, plus the same
pre-race features. On 2025-26 laps it beat the simulator's own lap model by
about 6% overall and 7-9% in the second half of a race.

``pace`` turns its next-lap prediction into what the simulator takes as a car's
pace: seconds per lap against the field, with the tyre part the simulator adds
itself (the circuit profile's compound offset and wear at the current age)
taken out, so it is not counted twice. Cars with no clean lap yet, or on wet
tyres, get NaN and keep the posterior pace.

Off unless an artifact says it passed its gate; the live replay backtest
(``scripts/backtest_live.py --lap-model``) is that gate.
"""

import json
import logging
import os
from typing import Mapping, Optional, Sequence

import numpy as np

from app.models.schemas import DriverFeatures
from app.services.ingestion_client import RaceLap
from app.services.model import feature_vector

logger = logging.getLogger(__name__)

LAP_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "lap_time_network.json")
DRY = ("SOFT", "MEDIUM", "HARD")


class LapModel:
    def __init__(self, network) -> None:
        self.network = network

    @classmethod
    def load(cls, path: str = LAP_MODEL_PATH) -> Optional["LapModel"]:
        """The linear model from the artifact, or None when there is none or it
        did not pass its gate."""
        from app.training.lap_time_network import LapTimeNetwork

        resolved = os.path.abspath(path)
        if not os.path.exists(resolved):
            return None
        with open(resolved) as handle:
            artifact = json.load(handle)
        if not artifact.get("linear_enabled") or "linear" not in artifact:
            logger.info("lap model present but not enabled")
            return None
        return cls(LapTimeNetwork.from_dict(artifact["linear"]))

    def pace(self, drivers: Sequence[DriverFeatures], laps: Sequence[RaceLap], upto: int,
             total_laps: int, profile: Mapping, season: int, round_number: int,
             archetype: str = "", weather: Optional[Mapping[str, float]] = None) -> np.ndarray:
        """Seconds per lap per car (field-relative level is arbitrary), NaN where
        the model has nothing to add."""
        from app.training.lap_time_network import LAP_FEATURES, next_lap_rows

        out = np.full(len(drivers), np.nan)
        rows, typical = next_lap_rows(season, round_number, laps, upto, total_laps,
                                      archetype, weather)
        if not rows or typical <= 0:
            return out
        seen = LAP_FEATURES.index("live_laps")
        tyres = profile.get("tyres", {})
        for i, d in enumerate(drivers):
            row = rows.get(d.driver)
            if row is None:
                continue
            x, compound, age = row
            if x[seen] <= 0 or compound not in DRY or compound not in tyres:
                continue
            y = float(self.network.predict(
                np.hstack([x, feature_vector(d, with_grid=True)])[None, :])[0])
            tyre = tyres[compound]["offset"] + tyres[compound]["wear"] * age
            out[i] = y * typical / 100.0 - tyre
        return out
