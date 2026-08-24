"""Scoring models — the public accuracy record.

Three ideas carry the honesty of this service:

**A raw Brier score is meaningless to a reader.** "0.043" is not a claim anyone
can evaluate. Every score is therefore reported alongside a baseline and a skill
score — how much better than the naive alternative the model actually did. A
model that cannot beat "everyone is equally likely" has earned no trust, and the
number should say so plainly.

**Calibration is a separate claim from accuracy.** A model can be accurate and
badly calibrated (right often, but wrong about how sure it is) or calibrated and
useless (honestly uncertain about everything). Both are reported.

**The windows are scored apart.** Pre-quali and post-quali forecasts answer
different questions; averaging them would hide exactly the comparison that makes
the pair interesting.
"""

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class Market(str, Enum):
    """The three binary questions each forecast answers per driver."""

    WIN = "win"
    PODIUM = "podium"
    POINTS = "points"


class DriverOutcome(BaseModel):
    driver: str
    position: int = 999
    classified: bool = False
    won: bool = False
    podium: bool = False
    points: bool = False


class RaceOutcome(BaseModel):
    """What actually happened. Written once the race results are ingested."""

    season: int
    round: int
    race_name: str = ""
    reconciled_at: datetime
    drivers: List[DriverOutcome] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.season}-{self.round}"


class MarketScore(BaseModel):
    """One market's score for one prediction.

    ``brier`` alone is not interpretable; ``skill_vs_baseline`` is the number
    worth showing a reader. Positive means the model beat the naive baseline,
    zero means it matched it, negative means it would have been better to guess.
    """

    market: Market
    brier: float = 0.0
    baseline_brier: float = 0.0
    skill_vs_baseline: float = 0.0
    log_score: Optional[float] = None
    drivers_scored: int = 0


class PredictionScore(BaseModel):
    """The scored record of a single locked forecast."""

    score_id: str
    prediction_id: str
    season: int
    round: int
    window: str
    model_version: str
    scored_at: datetime
    #: Carried through from the prediction so a degraded call stays identifiable
    #: in the aggregate rather than quietly flattering the average.
    data_complete: bool = True
    markets: List[MarketScore] = Field(default_factory=list)
    #: Drivers predicted but absent from the results, and vice versa. A forecast
    #: that missed half the field is not comparable to one that covered it.
    predicted_not_raced: List[str] = Field(default_factory=list)
    raced_not_predicted: List[str] = Field(default_factory=list)

    def market(self, market: Market) -> Optional[MarketScore]:
        return next((m for m in self.markets if m.market is market), None)


class CalibrationBucket(BaseModel):
    """One band of the calibration curve.

    ``predicted_mean`` vs ``observed_rate`` is the whole claim: of everything we
    called 30% likely, did roughly 30% happen?
    """

    lower: float
    upper: float
    count: int = 0
    predicted_mean: float = 0.0
    observed_rate: float = 0.0

    @property
    def gap(self) -> float:
        """Positive means overconfident — predicted more than happened."""
        return self.predicted_mean - self.observed_rate


class CalibrationCurve(BaseModel):
    market: Market
    window: Optional[str] = None
    model_version: Optional[str] = None
    samples: int = 0
    buckets: List[CalibrationBucket] = Field(default_factory=list)
    #: Mean |predicted - observed| across buckets, weighted by bucket size.
    #: One number for "how honest are the probabilities", lower is better.
    expected_calibration_error: float = 0.0


class WindowRecord(BaseModel):
    """Aggregate accuracy for one lock window."""

    window: str
    predictions_scored: int = 0
    brier_by_market: Dict[str, float] = Field(default_factory=dict)
    skill_by_market: Dict[str, float] = Field(default_factory=dict)
    mean_log_score: Optional[float] = None
    #: How often the most likely driver actually won. Intuitive, but a weak
    #: measure — it ignores everything the probabilities said about everyone
    #: else. Reported because readers expect it, never used to tune anything.
    top_pick_hit_rate: Optional[float] = None


class TrackRecord(BaseModel):
    """The public accuracy record.

    Deliberately includes ``predictions_pending``: a record that showed only
    scored predictions could be made to look better by never reconciling the
    bad ones.
    """

    generated_at: datetime
    seasons: List[int] = Field(default_factory=list)
    model_versions: List[str] = Field(default_factory=list)
    predictions_scored: int = 0
    predictions_pending: int = 0
    predictions_incomplete_data: int = 0
    windows: List[WindowRecord] = Field(default_factory=list)
    calibration: List[CalibrationCurve] = Field(default_factory=list)
