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
    DataQuality,
    DriverProbability,
    FeatureSnapshot,
    LockWindow,
    Market,
    ModelVersion,
    Prediction,
    published_markets,
)
from app.services import features as feature_builder
from app.services.ingestion_client import (
    GridSlot,
    IngestionClient,
    IngestionUnavailable,
)
from app.services.model import RaceModel
from app.services.storage import PredictionStore

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._client = client
        self._store = store
        self._model = model

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
        )
        if provisional:
            logger.warning(
                "grid for %s-%s is provisional (qualifying classification, "
                "penalties not applied)", season, round_number,
            )
        seed = derive_seed(season, round_number, window)

        return Prediction(
            prediction_id=str(uuid.uuid4()),
            season=season,
            round=round_number,
            race_name=race_name,
            window=window,
            locked_at=datetime.now(timezone.utc),
            race_start_utc=race_start_utc,
            window_opened_at=window_opened_at,
            model_version=self._model.version,
            seed=seed,
            driver_probabilities=_restrict_to_published(
                self._model.predict(snapshot.drivers, seed=seed), window
            ),
            published_markets=published_markets(window),
            feature_snapshot_ref=snapshot.snapshot_id,
            data_quality=quality,
        ), snapshot

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

        await self._store.save_snapshot(snapshot)
        await self._store.record_model_version(
            ModelVersion(
                version=self._model.version,
                created_at=datetime.now(timezone.utc),
                description="Plackett-Luce sampling over linear strength scores",
                parameters=self._model.parameters(),
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
