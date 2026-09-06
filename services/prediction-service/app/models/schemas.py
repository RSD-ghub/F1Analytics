"""Prediction models — the append-only record the whole product rests on.

A locked prediction is a historical fact. Once written it is never updated, and
it carries everything needed to reproduce it: the model version, the random seed,
and the exact feature values used. That is what makes the published track record
defensible — anyone can re-run a past call and get the same numbers, and nobody
(including us) can quietly improve a bad prediction after the fact.

Two windows are tracked separately because they are genuinely different
predictions. The pre-quali forecast has no grid information; the post-quali one
does. Comparing their accuracy measures exactly how much the grid tells you.
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class LockWindow(str, Enum):
    PRE_QUALI = "pre_quali"
    POST_QUALI = "post_quali"


class Market(str, Enum):
    WIN = "win"
    PODIUM = "podium"
    POINTS = "points"


#: Which markets each window is allowed to publish.
#:
#: The pre-quali window does **not** publish a win probability. Measured on two
#: held-out seasons its win-market skill was roughly zero with enormous variance
#: (-19.6% and +13.5%) — that is, no better than guessing which driver wins, and
#: sometimes considerably worse. Publishing a number we cannot beat a coin toss
#: with would poison the one thing this product is selling.
#:
#: Its podium (+28% to +38%) and points (+25% to +39%) forecasts are genuinely
#: skilful and are published. Once the grid is known, all three are.
MARKETS_BY_WINDOW = {
    LockWindow.PRE_QUALI: (Market.PODIUM, Market.POINTS),
    LockWindow.POST_QUALI: (Market.WIN, Market.PODIUM, Market.POINTS),
}


def published_markets(window: LockWindow) -> List[str]:
    return [market.value for market in MARKETS_BY_WINDOW[window]]


class DataQuality(BaseModel):
    """What was known to be missing when the forecast was locked.

    A prediction always publishes on schedule — a silent delay would be worse
    than a flagged forecast. But the gaps travel with it permanently, so the
    track record can account for calls made on incomplete information rather
    than pretending every prediction had the same footing.
    """

    complete: bool = True
    missing_sessions: List[str] = Field(default_factory=list)
    has_grid: bool = False
    #: True when the forecast used qualifying classification because the
    #: penalty-adjusted grid was not published yet. Any driver carrying a grid
    #: penalty is modelled from the wrong starting slot, so this must travel with
    #: the prediction rather than being resolved silently.
    grid_is_provisional: bool = False
    #: Which source the grid came from, when there was one — "official_final",
    #: "official_provisional", "race_result" or "qualifying". Stored so the
    #: track record can separate forecasts made on the real grid from those made
    #: on a stand-in, instead of only knowing that a stand-in was used.
    grid_source: str = ""
    notes: str = ""


class DriverFeatures(BaseModel):
    """One driver's point-in-time feature vector.

    Every value here must be derivable from data strictly before the target
    round. ``test_leakage`` enforces that by rebuilding these with all
    at-or-after data removed and asserting the result is byte-identical.
    """

    driver: str
    team: str = "Unknown"
    # Recent form, most recent races before the target round.
    avg_finish_recent: float = 0.0
    avg_finish_season: float = 0.0
    points_per_race: float = 0.0
    dnf_rate: float = 0.0
    team_avg_finish: float = 0.0
    circuit_avg_finish: float = 0.0
    races_completed: int = 0
    # Recent qualifying pace, from prior races. Available *before* this
    # weekend's qualifying, so it gives the pre-quali model a read on car pace
    # without needing the grid it is not allowed to see.
    avg_grid_recent: float = 0.0
    # This driver's recent average finish minus their team-mate's. The cleanest
    # available separation of driver from car: both share the machinery, so what
    # is left is mostly the driver.
    teammate_delta: float = 0.0
    # Mean positions gained from grid to flag — racecraft and starts, which
    # grid position alone cannot express.
    positions_gained: float = 0.0
    # Rolling qualifying deficit to pole, as a fraction of pole time. Known
    # before this weekend's qualifying, so both windows may use it — a read on
    # car pace rather than on one lap.
    recent_quali_gap_pct: float = 0.0
    # Rolling qualifying deficit to the team-mate. Same car, one lap, no
    # strategy — the cleanest driver-vs-car separation available.
    quali_teammate_gap_pct: float = 0.0
    # This weekend's qualifying deficit to pole. Post-quali only; 0.0 elsewhere,
    # which the model reads as "not supplied" because the grid block is off.
    quali_gap_pct: float = 0.0
    # This weekend's long-run practice deficit, as a fraction of the session
    # best. Practice runs after upgrades are fitted and before qualifying, so
    # unlike the grid this is legitimately available to *both* windows — it is
    # the only current-weekend pace signal the pre-quali model is allowed.
    practice_long_run_gap_pct: float = 0.0
    # Only populated in the post-quali window. 0 means "unknown", which is
    # exactly what it means pre-quali.
    grid_position: int = 0
    # Set when the driver has no prior history at all (a rookie, or round 1).
    is_cold_start: bool = False


class FeatureSnapshot(BaseModel):
    """The complete feature set used for one prediction, stored verbatim.

    Kept as its own document rather than embedded so a prediction stays small and
    the snapshot can be replayed independently. ``as_of_round`` is the audit
    handle: a snapshot for a round-12 forecast citing rounds up to 12 is provable
    leakage.
    """

    snapshot_id: str
    season: int
    round: int
    window: LockWindow
    #: Highest round whose data fed these features. Must be < ``round``.
    as_of_round: int
    #: Every round actually included — makes a data gap visible rather than silent.
    included_rounds: List[int] = Field(default_factory=list)
    created_at: datetime
    drivers: List[DriverFeatures] = Field(default_factory=list)


class DriverProbability(BaseModel):
    driver: str
    team: str = "Unknown"
    #: ``None`` when this window does not publish a win probability — which is
    #: a different statement from 0.0. Zero would be a confident claim that this
    #: driver cannot win; None says we are not making a claim at all. Scoring
    #: must skip it rather than read it as a number.
    p_win: Optional[float] = None
    p_podium: float = 0.0
    p_points: float = 0.0
    #: Expected finishing position — a readable summary, never the scored value.
    expected_position: float = 0.0


class Prediction(BaseModel):
    """An immutable, locked forecast."""

    prediction_id: str
    season: int
    round: int
    race_name: str = ""
    window: LockWindow
    locked_at: datetime
    #: When the race starts. Lets scoring confirm the lock preceded the race.
    race_start_utc: Optional[datetime] = None
    #: When this window opened. Stored so lateness stays derivable from the
    #: prediction alone: a forecast placed hours after its deadline had more of
    #: the world available than the deadline implies, and the record should show
    #: that rather than presenting every lock as equally timely.
    window_opened_at: Optional[datetime] = None
    model_version: str
    #: Seed used for the sampling. With the snapshot, this makes the call exactly
    #: reproducible.
    seed: int
    driver_probabilities: List[DriverProbability] = Field(default_factory=list)
    #: Markets this forecast actually claims. Stored on the prediction rather
    #: than inferred from the window, so a locked forecast stays self-describing
    #: if the policy changes later — a prediction made when a market was
    #: published must keep being scored on it, and one made when it was not must
    #: never be scored on it retroactively.
    published_markets: List[str] = Field(default_factory=list)
    feature_snapshot_ref: str
    data_quality: DataQuality = Field(default_factory=DataQuality)

    def publishes(self, market: str) -> bool:
        return market in self.published_markets

    @property
    def locked_late_by(self) -> Optional[timedelta]:
        """How long after its window opened this forecast was placed.

        None when the window time was not recorded (a manual lock). A large
        value is not invalid — it is a fact the track record should surface,
        since a late forecast had more of the world available than its deadline
        implies.
        """
        if self.window_opened_at is None:
            return None
        return self.locked_at - self.window_opened_at


class ModelVersion(BaseModel):
    """Changelog entry. Keeps the track record honest across model changes.

    Without this, a model swap silently rewrites the meaning of the accuracy
    history — old and new predictions get averaged together as though they came
    from the same system.
    """

    version: str
    created_at: datetime
    description: str = ""
    parameters: Dict[str, float] = Field(default_factory=dict)
    notes: str = ""


class ChampionshipOutcome(BaseModel):
    driver: str
    team: str = "Unknown"
    current_points: float = 0.0
    p_champion: float = 0.0
    expected_final_points: float = 0.0
    p_top_three: float = 0.0


class ChampionshipForecast(BaseModel):
    """Title probabilities from simulating every remaining race.

    Each iteration samples a finishing order for every remaining race from the
    per-race model, accumulates points, and records who ends up on top. Doing it
    ten thousand times turns "who wins the title?" into a frequency.
    """

    season: int
    as_of_round: int
    remaining_rounds: List[int] = Field(default_factory=list)
    runs: int = 0
    seed: int = 0
    model_version: str = ""
    generated_at: Optional[datetime] = None
    drivers: List[ChampionshipOutcome] = Field(default_factory=list)
    constructors: List[ChampionshipOutcome] = Field(default_factory=list)
