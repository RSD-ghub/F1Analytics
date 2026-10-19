"""Forecast orchestration: fetch → build features → run model → lock.

The rule that shapes everything here: **a lock window publishes on schedule, or
it does not publish at all.** It never waits for better data. A forecast that
slips until the data looks good is not a forecast — the deadline is what makes
the call falsifiable, and quietly moving it is how a track record becomes
meaningless.

So incomplete data produces a published prediction carrying a ``data_quality``
flag recording exactly what was missing. The one exception is the post-quali
window with no grid: that is not a degraded forecast, it is a different forecast
wearing the wrong label, and publishing it would corrupt the pre/post comparison
the product is built on.
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from app.models.schemas import (
    GRID_WINDOWS,
    MARKETS_BY_WINDOW,
    SPRINT_WINDOWS,
    DataQuality,
    DriverProbability,
    FeatureSnapshot,
    LockWindow,
    Market,
    ModelVersion,
    Prediction,
    RaceSimulation,
    SimulationStatus,
    published_markets,
    sprint_points_positions,
)
from app.services import features as feature_builder
from app.services import race_simulation
from app.services.neural_model import SIMULATION_RUNS
from app.services.ingestion_client import (
    GridSlot,
    IngestionClient,
    IngestionUnavailable,
)
from app.services.model import POINTS_POSITIONS, RaceModel
from app.services.storage import PredictionStore

logger = logging.getLogger(__name__)


#: A sprint's length as a fraction of a Grand Prix's: 100 km against the
#: 305 km minimum. Scales the retirement rate, which is measured per race.
SPRINT_DISTANCE_FRACTION = 100.0 / 305.0


class ConfirmedGridRequired(RuntimeError):
    """The final-grid window was asked to fire without a confirmed grid.

    Not an error to recover from by publishing anyway. This window exists
    precisely to be the forecast made on the real starting order; placing one on
    the qualifying classification would be a post-quali forecast wearing the
    wrong label, the same mislabelling the scheduler already refuses in the
    other direction for pre-quali.
    """


class GridRequired(RuntimeError):
    """A post-quali forecast was requested but no grid has been ingested."""


def derive_seed(season: int, round_number: int, window: LockWindow) -> int:
    """Deterministic per-forecast seed.

    Derived from identity rather than randomly, so a prediction is reproducible
    even if the stored seed were lost, and so re-running a backtest gives the
    same numbers every time.

    Uses sha256 rather than ``hash()`` deliberately: Python randomises string
    hashing per process (PYTHONHASHSEED), so ``hash()`` would produce a different
    seed on every restart. That would look perfectly stable in a single test run
    and silently destroy reproducibility in production — the exact failure this
    function exists to prevent.
    """
    key = "{}|{}|{}".format(season, round_number, window.value).encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest()[:4], "big") % (2**31)


class Predictor:
    def __init__(
        self,
        client: IngestionClient,
        store: PredictionStore,
        model: RaceModel,
        final_grid_model: Optional[RaceModel] = None,
        lap_engine=None,
    ) -> None:
        self._client = client
        self._store = store
        self._model = model
        # The lap-by-lap simulator, when fitted. It runs the race simulation
        # only if its artifact says it scored no worse than sampling.
        self._lap_engine = lap_engine
        # The neural model, when one is trained. It places the final-grid
        # forecast only once promoted; until then it is simulated alongside.
        self._final_grid_model = final_grid_model

    def model_for(self, window: LockWindow) -> RaceModel:
        """The model that places this window's forecast."""
        candidate = self._final_grid_model
        if (
            window is LockWindow.FINAL_GRID
            and candidate is not None
            and getattr(candidate, "promoted", False)
        ):
            return candidate
        return self._model

    async def build(
        self,
        season: int,
        round_number: int,
        window: LockWindow,
        circuit: str = "",
        race_start_utc: Optional[datetime] = None,
        race_name: str = "",
        window_opened_at: Optional[datetime] = None,
    ) -> Tuple[Prediction, FeatureSnapshot]:
        """Produce a forecast without persisting it.

        Returns the snapshot alongside it so ``lock`` can store both without
        rebuilding — and so a caller previewing a forecast can inspect exactly
        what fed it.
        """
        snapshot, quality = await self._prepare(
            season, round_number, window, circuit
        )
        seed = derive_seed(season, round_number, window)
        points_positions, distance = _race_terms(season, window)
        model = self.model_for(window)

        return Prediction(
            prediction_id=str(uuid.uuid4()),
            season=season,
            round=round_number,
            race_name=race_name,
            window=window,
            locked_at=datetime.now(timezone.utc),
            race_start_utc=race_start_utc,
            window_opened_at=window_opened_at,
            model_version=model.version,
            seed=seed,
            driver_probabilities=_restrict_to_published(
                model.predict(
                    snapshot.drivers, seed=seed,
                    points_positions=points_positions,
                    distance_fraction=distance,
                ),
                window,
            ),
            published_markets=published_markets(window),
            feature_snapshot_ref=snapshot.snapshot_id,
            data_quality=quality,
            points_positions=points_positions,
        ), snapshot

    async def simulate(
        self,
        season: int,
        round_number: int,
        circuit: str = "",
        race_name: str = "",
        runs: Optional[int] = None,
    ) -> RaceSimulation:
        """Simulate the Grand Prix on the confirmed grid with the neural model.

        The same preparation as the final-grid lock — the same features, the
        same refusal of an unconfirmed grid — so the simulation and the locked
        forecast are always about the same field. Never persisted.
        """
        base = dict(season=season, round=round_number, race_name=race_name)
        # Without a trained network the simulation still runs, on the
        # Plackett-Luce model itself: the same field, the same sampler and the
        # same 10,000 races, just without a second opinion to compare against.
        network = self._final_grid_model or self._model
        window = LockWindow.FINAL_GRID
        try:
            snapshot, quality = await self._prepare(
                season, round_number, window, circuit
            )
        except (ConfirmedGridRequired, GridRequired) as exc:
            # No grid to run on yet: a state to show, not an error to raise.
            return RaceSimulation(
                status=SimulationStatus.AWAITING_CONFIRMED_GRID,
                detail=str(exc), **base,
            )

        seed = derive_seed(season, round_number, window)
        points_positions, _ = _race_terms(season, window)
        compare = network is not self._model
        baseline = self._model.predict(
            snapshot.drivers, seed=seed, points_positions=points_positions
        ) if compare else None
        runs = runs or SIMULATION_RUNS
        engine = self._lap_engine
        outcomes = None
        if engine is not None and engine.enabled:
            outcomes = engine.sample_outcomes(
                network, snapshot.drivers, seed=seed, runs=runs, circuit=circuit)
        return RaceSimulation(
            engine=engine.version if outcomes is not None else "plackett-luce",
            engine_metrics=engine.metrics if outcomes is not None else {},
            status=SimulationStatus.READY,
            runs=runs,
            seed=seed,
            model_version=network.version,
            baseline_model_version=self._model.version if compare else "",
            model_promoted=bool(getattr(network, "promoted", False)),
            model_metrics=getattr(network, "metrics", {}) or {},
            generated_at=datetime.now(timezone.utc),
            data_quality=quality,
            drivers=race_simulation.simulate(
                network, snapshot.drivers, seed=seed, runs=runs,
                points_positions=points_positions, baseline=baseline,
                outcomes=outcomes,
            ),
            **base,
        )

    async def _prepare(
        self,
        season: int,
        round_number: int,
        window: LockWindow,
        circuit: str = "",
    ) -> Tuple[FeatureSnapshot, DataQuality]:
        """Fetch, refuse an unusable grid, and build the features."""
        results = await self._client.season_results(season)
        # Prior seasons carry career form and circuit history, both legitimately
        # known before the race.
        if season > 2010:
            try:
                results = list(results) + list(
                    await self._client.season_results(season - 1)
                )
            except IngestionUnavailable:
                logger.warning("prior season unavailable; using current season only")

        grid = await self._maybe_grid(season, round_number, window)
        if window in SPRINT_WINDOWS and not grid:
            raise GridRequired(
                "no sprint qualifying classification ingested for {}-{}; a "
                "sprint forecast is placed on the sprint grid or not at "
                "all".format(season, round_number)
            )
        if window in GRID_WINDOWS and not grid:
            raise GridRequired(
                "no grid ingested for {}-{}; a post-quali forecast without the "
                "grid is a pre-quali forecast with the wrong label".format(
                    season, round_number
                )
            )
        # Belt and braces on an irreversible write. Upstream can serve an entry
        # list with no classified positions, and a grid where nobody has a
        # starting slot is not a grid — locking on it would model every car from
        # last and the resulting forecast could never be corrected.
        if grid and not any(slot.effective < 999 for slot in grid):
            raise GridRequired(
                "grid for {}-{} has {} entries but no classified positions; "
                "qualifying results have not published yet".format(
                    season, round_number, len(grid)
                )
            )
        if window is LockWindow.FINAL_GRID and not all(
            slot.confirmed for slot in grid
        ):
            # The distinction this window exists for. A provisional grid is a
            # real, penalty-adjusted grid — and across nine measured events it
            # still differed from the final one six times, five of those moving
            # the pit-lane set. Publishing that here would make the final-grid
            # forecast indistinguishable from the post-quali one, which is
            # exactly the comparison the window was added to make.
            raise ConfirmedGridRequired(
                "grid for {}-{} is not confirmed ({}); the final-grid window "
                "waits for the FIA's grid rather than publishing on a "
                "stand-in".format(
                    season, round_number,
                    ", ".join(sorted({slot.grid_source for slot in grid})),
                )
            )

        snapshot = feature_builder.build_snapshot(
            results,
            season=season,
            target_round=round_number,
            window=window,
            circuit=circuit,
            grid=grid,
        )
        # A grid we had to infer from qualifying classification is not the grid.
        # Recorded on the prediction so a penalty-weekend forecast is
        # identifiable in the track record rather than quietly wrong.
        provisional = bool(grid) and not all(slot.confirmed for slot in grid)
        quality = await self._assess_quality(
            season,
            snapshot,
            bool(grid),
            grid_is_provisional=provisional,
            grid_source=_grid_source(grid),
            sprint=window in SPRINT_WINDOWS,
        )
        if provisional:
            logger.warning(
                "grid for %s-%s is provisional (qualifying classification, "
                "penalties not applied)", season, round_number,
            )
        return snapshot, quality

    async def lock(
        self,
        season: int,
        round_number: int,
        window: LockWindow,
        circuit: str = "",
        race_start_utc: Optional[datetime] = None,
        race_name: str = "",
        window_opened_at: Optional[datetime] = None,
    ) -> Prediction:
        """Build and permanently record a forecast.

        Raises ``PredictionExists`` if this window is already locked — re-locking
        would be an edit to the public record.
        """
        prediction, snapshot = await self.build(
            season, round_number, window, circuit, race_start_utc, race_name,
            window_opened_at,
        )

        model = self.model_for(window)
        await self._store.save_snapshot(snapshot)
        await self._store.record_model_version(
            ModelVersion(
                version=model.version,
                created_at=datetime.now(timezone.utc),
                description=(
                    "Plackett-Luce sampling over linear strength scores"
                    if model is self._model else
                    "Plackett-Luce sampling over neural-network strength scores"
                ),
                parameters=model.parameters(),
            )
        )
        stored = await self._store.insert_prediction(prediction)
        logger.info(
            "locked %s forecast for %s-%s (%s drivers, complete=%s)",
            window.value,
            season,
            round_number,
            len(prediction.driver_probabilities),
            prediction.data_quality.complete,
        )
        return stored

    # ── Helpers ──────────────────────────────────────────────────────────────

    async def _maybe_grid(
        self, season: int, round_number: int, window: LockWindow
    ) -> Optional[Sequence[GridSlot]]:
        """Fetch the grid only for the window entitled to see it."""
        if window not in GRID_WINDOWS:
            return None
        try:
            if window in SPRINT_WINDOWS:
                return await self._client.sprint_grid(season, round_number)
            return await self._client.grid(season, round_number)
        except IngestionUnavailable as exc:
            logger.warning("grid fetch failed for %s-%s: %s", season, round_number, exc)
            return None

    async def _assess_quality(
        self,
        season: int,
        snapshot: FeatureSnapshot,
        has_grid: bool,
        grid_is_provisional: bool = False,
        grid_source: str = "",
        sprint: bool = False,
    ) -> DataQuality:
        """Record what was missing, without blocking publication.

        A completeness check that cannot run is itself a data-quality problem and
        is reported as one, rather than being allowed to pass as "fine".
        """
        expected = list(range(1, snapshot.round))
        local_gaps = [
            "{}-{}".format(season, r)
            for r in expected
            if r not in snapshot.included_rounds
        ]

        try:
            # Results depth, not full. prediction-service consumes race
            # classifications and qualifying — nothing else. Asking whether the
            # archive is complete at *lap* depth flags every forecast as built
            # on incomplete data whenever laps have not been backfilled, which
            # is both wrong and corrosive: a caveat that is always present is a
            # caveat nobody reads.
            status = await self._client.completeness(season, season, depth="results")
            upstream_gaps = [
                gap for gap in status.open_gaps if _is_before(gap, season, snapshot.round)
            ]
        except IngestionUnavailable as exc:
            return DataQuality(
                complete=False,
                missing_sessions=local_gaps,
                has_grid=has_grid,
                grid_is_provisional=grid_is_provisional,
                grid_source=grid_source,
                notes="completeness check unavailable: {}".format(exc),
            )

        missing = sorted(set(local_gaps) | set(upstream_gaps))
        notes = []
        if missing:
            notes.append("{} prior round(s) missing or incomplete".format(len(missing)))
        if grid_is_provisional:
            notes.append(
                "grid is the sprint qualifying classification; penalties not "
                "applied" if sprint else
                "grid is qualifying classification; penalties not yet applied"
            )
        return DataQuality(
            complete=not missing and not grid_is_provisional,
            missing_sessions=missing,
            has_grid=has_grid,
            grid_is_provisional=grid_is_provisional,
            grid_source=grid_source,
            notes="; ".join(notes),
        )


def _race_terms(season: int, window: LockWindow) -> Tuple[int, float]:
    """Places that score, and the race's length as a fraction of a Grand Prix."""
    if window in SPRINT_WINDOWS:
        return sprint_points_positions(season), SPRINT_DISTANCE_FRACTION
    return POINTS_POSITIONS, 1.0


def _restrict_to_published(
    probabilities: Sequence[DriverProbability], window: LockWindow
) -> List[DriverProbability]:
    """Blank out markets this window does not publish.

    The model always computes a win probability — the championship simulation
    needs full finishing orders, and that is an internal aggregate rather than a
    per-race claim. What changes here is only what gets *published and scored*.

    Set to ``None``, never 0.0. Zero is a confident assertion that a driver
    cannot win; None is the absence of a claim, and scoring treats the two very
    differently.
    """
    if Market.WIN in MARKETS_BY_WINDOW[window]:
        return list(probabilities)
    return [row.model_copy(update={"p_win": None}) for row in probabilities]


def _is_before(gap_key: str, season: int, target_round: int) -> bool:
    """Only gaps in rounds this forecast depends on are relevant.

    A hole in round 15 does not degrade a round-5 forecast, and flagging it would
    train everyone to ignore the flag.
    """
    try:
        gap_season, gap_round = (int(part) for part in gap_key.split("-", 1))
    except (ValueError, TypeError):
        return False
    return gap_season == season and gap_round < target_round


def _grid_source(grid) -> str:
    """The provenance shared by the grid, or "mixed" if the rows disagree.

    Rows should always agree — a grid is applied all-or-nothing — so "mixed" is
    a bug signal rather than a normal value, and worth being able to see in the
    stored prediction rather than averaging away.
    """
    if not grid:
        return ""
    sources = {slot.grid_source for slot in grid}
    return sources.pop() if len(sources) == 1 else "mixed"
