"""The race model: features → a calibrated distribution over finishing orders.

**Design.** Each driver's features are reduced to a single *strength* score by a
linear combination; strengths become a distribution over finishing orders by
Monte Carlo, perturbing each with Gumbel noise and ranking. Sampling that way is
exactly a Plackett-Luce model — the standard way to turn scores into a
distribution over permutations — which is what lets the weights be fitted by
maximum likelihood on real finishing orders (see ``app/training``).

**The weights are fitted, not chosen.** An earlier version hand-picked them and
tuned only a temperature; it scored *worse than uniform guessing* on the win
market. Weights now come from ``model_weights.json``, fitted on three decades of
races. If that artifact is missing the model refuses to load rather than falling
back to guesses — a silent fallback to untrained weights would publish
predictions that look identical to trained ones.

**One definition of the feature vector, used by training and serving.**
``feature_vector`` below is called by both. Two implementations that drift apart
— the classic train/serve skew bug — would produce a model that scores well in
training and is quietly wrong in production, with nothing in the output to show
it.

**Era enters as an interaction, never as a plain term.** Plackett-Luce depends
only on strength *differences* within a race, and era is constant across a race,
so an additive era feature would cancel exactly and change nothing. What does
vary is how much a grid slot is worth: overtaking difficulty differs sharply
between the pre-DRS, DRS and ground-effect eras. So grid position is interacted
with era bucket, letting the fit learn the modern grid effect without averaging
it against a 1994 race.
"""

import json
import logging
import os
from typing import Dict, List, Optional, Sequence

import numpy as np

from app.models.schemas import DriverFeatures, DriverProbability

logger = logging.getLogger(__name__)

#: Bump on any change that alters output for the same input. The track record is
#: grouped by this, so a silent change would blend two different models'
#: accuracy into one misleading number.
#:
#: v4 — adds qualifying time gaps and practice long-run pace, and separates
#:      qualifying classification from the penalty-adjusted starting grid.
#:      Trained from the Mongo corpus via ingestion-service rather than from
#:      side-loaded files. Held-out 2024/25: post-quali win skill +19.6% /
#:      +18.7% (was +17.6% / +15.5%), podium up to +47.7%.
#: v3 — weights fitted by Plackett-Luce MLE, era-weighted samples, era × grid
#:      interaction, per-window temperature.
#: v2 — hand-picked weights, swept temperature. Not comparable.
#: v1 — hand-picked weights, untuned temperature. Scored worse than guessing.
MODEL_VERSION = "race-plackett-luce-v4"

POINTS_POSITIONS = 10
PODIUM_POSITIONS = 3

#: Features available in both lock windows.
BASE_FEATURES = (
    "avg_finish_recent",
    "avg_finish_season",
    "team_avg_finish",
    "circuit_avg_finish",
    "points_per_race",
    "dnf_rate",
    "is_cold_start",
    "avg_grid_recent",
    "teammate_delta",
    "positions_gained",
    "recent_quali_gap_pct",
    "quali_teammate_gap_pct",
    "practice_long_run_gap_pct",
)

#: Grid position, split by era. Only one is non-zero for any given race — the
#: bucket that race belongs to — so these act as one feature with an era-specific
#: weight. At serving time only ``grid_modern`` is ever active.
GRID_FEATURES = ("grid_legacy", "grid_modern", "quali_gap_pct")

SERVING_ERA_BUCKET = "modern"

WEIGHTS_PATH = os.path.join(os.path.dirname(__file__), "..", "model_weights.json")


def feature_names(with_grid: bool) -> List[str]:
    return list(BASE_FEATURES) + (list(GRID_FEATURES) if with_grid else [])


def feature_vector(
    driver: DriverFeatures,
    with_grid: bool,
    era_bucket: str = SERVING_ERA_BUCKET,
) -> np.ndarray:
    """Map one driver's features to the model's input vector.

    The single definition shared by training and serving. Anything added here is
    automatically used by both, which is the point.
    """
    values = [
        driver.avg_finish_recent,
        driver.avg_finish_season,
        driver.team_avg_finish,
        driver.circuit_avg_finish,
        driver.points_per_race,
        driver.dnf_rate,
        1.0 if driver.is_cold_start else 0.0,
        driver.avg_grid_recent,
        driver.teammate_delta,
        driver.positions_gained,
        driver.recent_quali_gap_pct,
        driver.quali_teammate_gap_pct,
        driver.practice_long_run_gap_pct,
    ]
    if with_grid:
        grid = float(driver.grid_position)
        # Grid slot is era-split (overtaking difficulty changed); the time gap
        # is not, because a tenth off pole means the same thing in any era.
        values.extend([
            grid if era_bucket == "legacy" else 0.0,
            grid if era_bucket == "modern" else 0.0,
            driver.quali_gap_pct,
        ])
    return np.array(values, dtype=float)


class ModelWeightsMissing(RuntimeError):
    """No fitted weights artifact. The service must not serve guesses."""


class RaceModel:
    """Turns point-in-time features into finishing probabilities."""

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        means: Optional[Dict[str, float]] = None,
        stds: Optional[Dict[str, float]] = None,
        noise_scale: float = 1.0,
        runs: int = 20000,
        version: str = MODEL_VERSION,
        pre_quali_weights: Optional[Dict[str, float]] = None,
        pre_quali_noise_scale: Optional[float] = None,
    ) -> None:
        self.weights = weights or {}
        # Two fitted weight sets, because they are two different models. The
        # post-quali base weights were fitted *conditional on grid position
        # being present*: with a strong grid term in the fit, the other features
        # only have to explain what the grid does not. Reusing them without a
        # grid would apply weights that assume information the pre-quali window
        # does not have.
        self.pre_quali_weights = pre_quali_weights or {}
        self.means = means or {}
        self.stds = stds or {}
        # Fitted weights carry their own scale — under the Plackett-Luce
        # likelihood, larger weights *are* more confidence. So this stays at 1.0
        # for a trained model; it exists only as an override for experiments.
        self.noise_scale = noise_scale
        # Separate confidence for the grid-blind window. The two windows carry
        # different amounts of information, so they need different sharpening:
        # a single shared temperature tuned for post-quali made the pre-quali
        # model confidently wrong, taking its win-market skill to -34%.
        self.pre_quali_noise_scale = (
            noise_scale if pre_quali_noise_scale is None else pre_quali_noise_scale
        )
        self.runs = runs
        self._version = version

    # ── Loading ──────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: str = WEIGHTS_PATH, runs: int = 20000) -> "RaceModel":
        """Load fitted weights. Raises if the artifact is absent."""
        resolved = os.path.abspath(path)
        if not os.path.exists(resolved):
            raise ModelWeightsMissing(
                "no fitted weights at {}; run scripts/train_model.py. Serving "
                "untrained weights would publish predictions indistinguishable "
                "from trained ones.".format(resolved)
            )
        with open(resolved) as handle:
            artifact = json.load(handle)

        logger.info(
            "loaded model %s fitted %s on seasons %s",
            artifact.get("version"),
            artifact.get("fitted_at"),
            artifact.get("training_seasons"),
        )
        return cls(
            weights=artifact["weights"],
            means=artifact.get("feature_means", {}),
            stds=artifact.get("feature_stds", {}),
            noise_scale=artifact.get("noise_scale", 1.0),
            runs=runs,
            version=artifact.get("version", MODEL_VERSION),
            pre_quali_weights=artifact.get("pre_quali_weights", {}),
            pre_quali_noise_scale=artifact.get("pre_quali_noise_scale"),
        )

    @property
    def version(self) -> str:
        return self._version

    @property
    def uses_grid(self) -> bool:
        return any(name in self.weights for name in GRID_FEATURES)

    def parameters(self) -> Dict[str, float]:
        """Recorded on the prediction so the exact configuration is auditable."""
        return dict(self.weights, noise_scale=self.noise_scale, runs=float(self.runs))

    # ── Strength ─────────────────────────────────────────────────────────────

    def standardise(self, names: Sequence[str], values: np.ndarray) -> np.ndarray:
        """Apply the training-set scaling.

        Stored with the weights rather than recomputed, so a prediction for a
        single race is scaled exactly as training was — recomputing from the
        current field would make a driver's strength depend on who else entered.
        """
        scaled = np.empty_like(values)
        for index, name in enumerate(names):
            mean = self.means.get(name, 0.0)
            std = self.stds.get(name, 1.0) or 1.0
            scaled[index] = (values[index] - mean) / std
        return scaled

    def active_weights(self, with_grid: bool) -> Dict[str, float]:
        """Whichever fitted set matches the information actually available."""
        if with_grid or not self.pre_quali_weights:
            return self.weights
        return self.pre_quali_weights

    def strength(self, driver: DriverFeatures) -> float:
        with_grid = self.uses_grid and driver.grid_position > 0
        names = feature_names(with_grid)
        weights = self.active_weights(with_grid)
        vector = self.standardise(
            names, feature_vector(driver, with_grid, SERVING_ERA_BUCKET)
        )
        return float(
            sum(weights.get(name, 0.0) * value for name, value in zip(names, vector))
        )

    # ── Sampling ─────────────────────────────────────────────────────────────

    def predict(
        self, drivers: Sequence[DriverFeatures], seed: int
    ) -> List[DriverProbability]:
        """Finishing probabilities for a field.

        p_win sums to 1.0 across the field by construction — exactly one driver
        wins each simulated race.
        """
        if not drivers:
            return []
        return self._summarise(
            drivers, self.sample_orders(drivers, seed=seed, runs=self.runs)
        )

    def sample_orders(
        self, drivers: Sequence[DriverFeatures], seed: int, runs: int
    ) -> np.ndarray:
        """Sample ``runs`` finishing orders. Shape: (runs, n_drivers) of positions.

        Gumbel-perturbed strengths ranked descending is exact sampling from a
        Plackett-Luce distribution — the noise is the model, not a hack.
        Retirements are pushed behind every finisher rather than removed, so each
        row stays a complete ranking.
        """
        rng = np.random.default_rng(seed)
        strengths = np.array([self.strength(d) for d in drivers], dtype=float)
        # Which window this is, inferred from whether a grid was supplied.
        scale = (
            self.noise_scale
            if any(d.grid_position > 0 for d in drivers)
            else self.pre_quali_noise_scale
        )
        dnf_rates = np.array([min(max(d.dnf_rate, 0.0), 0.9) for d in drivers])

        gumbel = rng.gumbel(loc=0.0, scale=scale, size=(runs, len(drivers)))
        perturbed = strengths[None, :] + gumbel

        retired = rng.random((runs, len(drivers))) < dnf_rates[None, :]
        perturbed = np.where(retired, -np.inf, perturbed)

        order = np.argsort(-perturbed, axis=1)
        positions = np.empty_like(order)
        rows = np.arange(runs)[:, None]
        positions[rows, order] = np.arange(1, len(drivers) + 1)[None, :]
        return positions

    def _summarise(
        self, drivers: Sequence[DriverFeatures], positions: np.ndarray
    ) -> List[DriverProbability]:
        runs = positions.shape[0]
        results = [
            DriverProbability(
                driver=driver.driver,
                team=driver.team,
                p_win=float((positions[:, index] == 1).sum() / runs),
                p_podium=float((positions[:, index] <= PODIUM_POSITIONS).sum() / runs),
                p_points=float((positions[:, index] <= POINTS_POSITIONS).sum() / runs),
                expected_position=float(positions[:, index].mean()),
            )
            for index, driver in enumerate(drivers)
        ]
        results.sort(key=lambda r: (-r.p_win, r.driver))
        return results
