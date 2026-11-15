"""The car's form, tracked race by race.

``team_avg_finish`` averages the whole season so far; ``recent_quali_gap_pct``
averages the last five sessions. Both treat a result from March the same as one
from last Sunday, so both trail a car that has just found half a second — and
both carry last year's car into a new season at full confidence.

A Kalman filter per car fixes both:

* **After every race**, the estimate moves toward what the car just did, by an
  amount set by how sure the estimate already was: early in a season it moves
  a lot, after a run of consistent races it moves little.
* **At a new season** the estimate's uncertainty is widened, and widened much
  more at a regulation reset, so the first races of a new rulebook re-learn the
  order quickly instead of being anchored to cars that no longer exist.

Two filters, two features, both from strictly earlier races:

* ``team_form_quali_pct`` — the car's qualifying pace: the better of its two
  cars' gaps to pole each weekend. Qualifying is the cleanest read on car
  speed there is: one lap, low fuel, no strategy.
* ``team_form_finish`` — the car's finishing level: the mean classified finish
  of its cars each race. Retirements are not results here; reliability is
  ``dnf_rate``'s job.

Matched by organisation (``team_lineage``), so a rename keeps its history.
"""

from collections import defaultdict
from typing import Dict, Iterable, List, Sequence, Tuple

from app.services import team_lineage
from app.services.ingestion_client import RaceResult
from app.training import eras

#: Fitted on the training seasons (see ``WITHHELD_FEATURES`` / model notes).
#: Variances are in each filter's own units: squared fraction of pole time for
#: qualifying, squared places for finishing.
QUALI = {
    "neutral": 0.015, "prior_variance": 1e-4,
    "race_variance": 2e-6, "season_variance": 2e-5, "reset_variance": 1e-4,
    "observation_variance": 2.5e-5,
}
FINISH = {
    "neutral": 10.5, "prior_variance": 16.0,
    "race_variance": 0.15, "season_variance": 2.0, "reset_variance": 10.0,
    "observation_variance": 9.0,
}


class _Filter:
    __slots__ = ("mean", "variance", "season")

    def __init__(self, settings: Dict[str, float], season: int) -> None:
        self.mean = settings["neutral"]
        self.variance = settings["prior_variance"]
        self.season = season


def _run(
    observations: Sequence[Tuple[Tuple[int, int], str, float]],
    settings: Dict[str, float],
) -> Dict[str, _Filter]:
    """Filter every car's observations in race order; return the final state."""
    state: Dict[str, _Filter] = {}
    for (season, _), car, value in sorted(observations, key=lambda o: o[0]):
        f = state.get(car)
        if f is None:
            f = state[car] = _Filter(settings, season)
        if season != f.season:
            reset = eras.era_for(season) != eras.era_for(f.season)
            f.variance += settings["reset_variance" if reset else "season_variance"]
            f.season = season
        f.variance += settings["race_variance"]
        gain = f.variance / (f.variance + settings["observation_variance"])
        f.mean += gain * (value - f.mean)
        f.variance *= 1.0 - gain
    return state


def quali_observations(
    gaps: Dict[Tuple[int, int], Dict[str, float]],
    teams: Dict[Tuple[int, int], Dict[str, str]],
) -> List[Tuple[Tuple[int, int], str, float]]:
    """(race, car, the better of its two cars' gaps to pole)."""
    out = []
    for key, by_driver in gaps.items():
        entries = teams.get(key, {})
        best: Dict[str, float] = {}
        for driver, gap in by_driver.items():
            car = team_lineage.lineage_of(entries.get(driver, ""))
            if car:
                best[car] = min(best.get(car, gap), gap)
        out.extend((key, car, gap) for car, gap in best.items())
    return out


def finish_observations(results: Iterable[RaceResult]) -> List[Tuple[Tuple[int, int], str, float]]:
    """(race, car, mean classified finishing position of its cars)."""
    by_car = defaultdict(list)
    for row in results:
        if row.classified:
            by_car[((row.season, row.round), team_lineage.lineage_of(row.team))].append(
                float(row.position))
    return [(key, car, sum(v) / len(v)) for (key, car), v in by_car.items()]


def form_before(
    history: Sequence[RaceResult],
    quali_gaps: Dict[Tuple[int, int], Dict[str, float]],
    season: int,
    target_round: int,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Each car's (qualifying form, finishing form) going into the target race.

    ``history`` must already be cut at the target round; qualifying gaps are
    cut here the same way. Keyed by organisation.
    """
    teams = defaultdict(dict)
    for row in history:
        teams[(row.season, row.round)][row.driver] = row.team
    earlier = {
        key: value for key, value in quali_gaps.items()
        if key[0] < season or (key[0] == season and key[1] < target_round)
    }
    quali_state = _run(quali_observations(earlier, teams), QUALI)
    finish_state = _run(finish_observations(history), FINISH)
    return ({car: f.mean for car, f in quali_state.items()},
            {car: f.mean for car, f in finish_state.items()})


def for_team(form: Dict[str, float], team: str, settings: Dict[str, float]) -> float:
    return form.get(team_lineage.lineage_of(team), settings["neutral"])
