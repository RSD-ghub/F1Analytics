"""Champion/challenger promotion — the gate on continuous retraining.

Every race adds ground truth, so the model should keep improving. The loop is:
reconcile a race → data grows → retrain a challenger → **prove it beats the
incumbent** → promote. Without that middle step it is not a feedback loop, it is
a random walk with a version number.

**Why this loop is safe.** Self-reinforcing systems usually fail because the
model trains on its own output and drifts into confirming itself. This one
cannot: the training signal is finishing positions from real races, which our
forecasts have no influence over. Reality is the teacher, so the loop converges
on the sport rather than on the model's opinion of the sport.

**The failure it must actually guard against is holdout burn.** Repeatedly
retraining, testing on the same held-out races, and shipping whichever candidate
scores best is a slow way to overfit that holdout. Each peek spends a little of
its validity, and after twenty cycles the "held-out" score is a training score
wearing a disguise. So evaluation runs only on races *newer than the champion's
own evaluation window* — every promotion decision is made on evidence no earlier
decision has seen.

**And a margin, not a tie-break.** Scores on twenty-odd races are noisy. Promoting
on any improvement at all would swap models on coin flips, and the track record
would fragment into versions that differ by nothing.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

import numpy as np

from app.training.plackett_luce import (
    RaceObservation,
    mean_log_likelihood,
    uniform_baseline_log_likelihood,
)

logger = logging.getLogger(__name__)

#: A challenger must beat the champion by at least this fraction of the
#: champion's improvement over uniform. Two percent of a real edge is signal;
#: anything smaller is sampling noise on a few dozen races.
MIN_RELATIVE_GAIN = 0.02

#: Below this, a comparison is not worth acting on regardless of the margin.
#: Roughly a season of racing.
MIN_EVALUATION_RACES = 15


@dataclass
class PromotionDecision:
    """The full reasoning behind a promote-or-hold, recorded either way.

    Rejections are kept as deliberately as promotions: a run of them is the
    signal that the features have stopped improving, which is worth knowing and
    is invisible if only successes are logged.
    """

    promote: bool
    reason: str
    champion_score: float
    challenger_score: float
    baseline_score: float
    relative_gain: float
    evaluation_races: int
    evaluated_seasons: List[int] = field(default_factory=list)
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> Dict:
        return {
            "promote": self.promote,
            "reason": self.reason,
            "champion_score": self.champion_score,
            "challenger_score": self.challenger_score,
            "baseline_score": self.baseline_score,
            "relative_gain": self.relative_gain,
            "evaluation_races": self.evaluation_races,
            "evaluated_seasons": self.evaluated_seasons,
            "decided_at": self.decided_at.isoformat(),
        }


def unseen_races(
    observations: Sequence[RaceObservation],
    champion_trained_through: Optional[int],
    champion_evaluated_through: Optional[int],
) -> List[RaceObservation]:
    """Races eligible to decide a promotion.

    A race qualifies only if it is newer than everything the champion was
    trained on *and* newer than the window that promoted the champion. The
    second condition is the one that stops holdout burn: without it, every
    retraining cycle would re-test on the same races and eventually promote a
    model that had simply been lucky on them enough times.
    """
    floor = max(
        champion_trained_through or 0,
        champion_evaluated_through or 0,
    )
    return [o for o in observations if o.season > floor]


def compare(
    champion_weights: Dict[str, float],
    challenger_weights: Dict[str, float],
    feature_names: Sequence[str],
    races: Sequence[RaceObservation],
    min_races: int = MIN_EVALUATION_RACES,
    min_gain: float = MIN_RELATIVE_GAIN,
) -> PromotionDecision:
    """Decide whether the challenger replaces the champion.

    Both are scored on the same races with the same metric — mean log-likelihood
    of the observed finishing orders — and the margin is expressed relative to
    how much the champion beats a uniform baseline, so "2% better" means 2% of a
    real edge rather than 2% of an arbitrary number.
    """
    if len(races) < min_races:
        return PromotionDecision(
            promote=False,
            reason=(
                "only {} unseen race(s); need {} before a promotion decision "
                "carries any weight".format(len(races), min_races)
            ),
            champion_score=0.0,
            challenger_score=0.0,
            baseline_score=0.0,
            relative_gain=0.0,
            evaluation_races=len(races),
        )

    champion = _score(champion_weights, feature_names, races)
    challenger = _score(challenger_weights, feature_names, races)
    baseline = uniform_baseline_log_likelihood(races)

    champion_edge = champion - baseline
    if champion_edge <= 0:
        # The incumbent is no better than guessing, so any improvement is worth
        # taking; the relative-margin test would divide by ~zero here.
        gain = 1.0 if challenger > champion else -1.0
    else:
        gain = (challenger - champion) / abs(champion_edge)

    seasons = sorted({o.season for o in races})
    # Coerced to native Python types. numpy scalars leak in through the scoring
    # helpers, and json.dump rejects np.bool_ / np.float64 outright — the
    # promotion history would have failed to write at the moment it mattered.
    promote = bool(gain >= min_gain)
    gain = float(gain)

    return PromotionDecision(
        promote=promote,
        reason=(
            "challenger beats champion by {:.1%} of its edge over uniform".format(gain)
            if promote
            else "gain of {:.1%} is below the {:.0%} threshold; keeping champion".format(
                gain, min_gain
            )
        ),
        champion_score=float(champion),
        challenger_score=float(challenger),
        baseline_score=float(baseline),
        relative_gain=gain,
        evaluation_races=len(races),
        evaluated_seasons=seasons,
    )


def _score(
    weights: Dict[str, float],
    feature_names: Sequence[str],
    races: Sequence[RaceObservation],
) -> float:
    vector = np.array([weights.get(name, 0.0) for name in feature_names])
    return mean_log_likelihood(vector, races)
