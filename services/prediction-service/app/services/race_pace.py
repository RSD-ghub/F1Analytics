"""Race pace from lap times — what a finishing position cannot say.

A finishing order squeezes seventy laps into one ranking, and folds into it
everything that is not pace: a safety car at the wrong moment, a slow stop, a
first-lap tangle, a strategy gamble. Lap times measure pace directly, and there
are about a thousand of them per race for every one finishing order.

**Per race, a fuel- and tyre-corrected pace for every driver.** Raw lap times
are not comparable: a car is ~3 seconds faster on its last lap than its first
because it has burned the fuel, and a 30-lap-old hard tyre is slower than a
fresh soft. So each race is fitted as

    lap_time = driver pace + fuel × lap + compound offset + compound wear × tyre age

by least squares over that race's clean laps, and the driver terms are the pace
estimate. Expressed as the fractional gap to the fastest driver, for the same
reason qualifying gaps are: a tenth at Monaco is not a tenth at Spa.

**Clean laps only.** Dropped before fitting: the first lap (standing start,
traffic), out- and in-laps around a stop, laps run behind a safety car or VSC
(found from the field itself — any lap where the median car is markedly slow),
and laps far off the driver's own pace (an incident, a spin, a slow puncture).
After a first fit, laps with large residuals are dropped and the race refitted,
so one lap of traffic cannot move a driver's estimate.

**Two features come out of it, both strictly from prior races:**

* ``race_pace_recent_pct`` — the driver's mean corrected gap over recent races.
* ``team_race_pace_pct`` — the car's: the better of its two drivers each race,
  averaged over the team's recent races. Team-mates share the machinery, and
  the car is most of what race pace measures.

Lap timing exists from 2018. Earlier races carry the neutral value for every
driver, which is constant across the field and so cancels out of a
Plackett-Luce likelihood exactly: those races neither help nor hurt the fit of
these weights.
"""

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from app.services import team_lineage
from app.services.ingestion_client import RaceLap

#: Stand-in when there is no lap history: a midfield deficit, not zero, which
#: would read as "fastest car in the race".
NEUTRAL_GAP_PCT = 0.015

#: Races that count toward recent pace. Matches the results form window.
PACE_FORM_WINDOW = 5

#: Gaps beyond this are a broken estimate, not a slow car.
MAX_PLAUSIBLE_GAP_PCT = 0.05

#: A lap whose field median is this much slower than the race's typical lap
#: was run under a safety car, VSC or red flag, for everyone.
NEUTRALISED_FACTOR = 1.05

#: A lap this far off the race's typical pace is an incident, not pace.
OUTLIER_FACTOR = 1.07

#: After the first fit, laps further than this many robust standard deviations
#: from the model are dropped and the race refitted.
RESIDUAL_CUTOFF = 3.0

MIN_CLEAN_LAPS_PER_DRIVER = 8
MIN_DRIVERS = 6

_UNUSABLE_COMPOUNDS = {"", "nan", "none", "unknown", "test_unknown"}


def _clean_laps(laps: Sequence[RaceLap]) -> List[RaceLap]:
    """The laps that measure pace, per the rules in the module docstring."""
    timed = [
        lap for lap in laps
        if lap.lap_time_seconds > 0 and lap.lap >= 2
        and (lap.compound or "").strip().lower() not in _UNUSABLE_COMPOUNDS
    ]
    if not timed:
        return []

    # In- and out-laps: the first lap of every stint after the first, and the
    # last lap of every stint the driver later left.
    stints: Dict[Tuple[str, int], List[int]] = {}
    for lap in timed:
        stints.setdefault((lap.driver, lap.stint), []).append(lap.lap)
    last_stint = {}
    for driver, stint in stints:
        last_stint[driver] = max(last_stint.get(driver, stint), stint)
    pit_laps = set()
    for (driver, stint), numbers in stints.items():
        if stint > min(s for d, s in stints if d == driver):
            pit_laps.add((driver, min(numbers)))
        if stint < last_stint[driver]:
            pit_laps.add((driver, max(numbers)))

    # Neutralised laps, seen in the field rather than read from messages.
    by_lap: Dict[int, List[float]] = {}
    for lap in timed:
        by_lap.setdefault(lap.lap, []).append(lap.lap_time_seconds)
    lap_medians = {number: float(np.median(times)) for number, times in by_lap.items()}
    typical = float(np.median(list(lap_medians.values())))
    neutralised = {
        number for number, median in lap_medians.items()
        if median > NEUTRALISED_FACTOR * typical
    }

    return [
        lap for lap in timed
        if (lap.driver, lap.lap) not in pit_laps
        and lap.lap not in neutralised
        and lap.lap_time_seconds <= OUTLIER_FACTOR * typical
    ]


def _tyre_age(laps: Sequence[RaceLap]) -> Dict[Tuple[str, int], int]:
    """Laps into the stint, from the stint's first timed lap.

    Approximate for a stint started on a used set, which lap data does not say.
    """
    first: Dict[Tuple[str, int], int] = {}
    for lap in laps:
        key = (lap.driver, lap.stint)
        first[key] = min(first.get(key, lap.lap), lap.lap)
    return {
        (lap.driver, lap.lap): lap.lap - first[(lap.driver, lap.stint)] + 1
        for lap in laps
    }


class RaceFit:
    """One race's fitted lap-time model: pace per driver, fuel, tyres, noise."""

    __slots__ = ("pace", "fuel_per_lap", "compound_offset", "wear_per_lap",
                 "lap_noise", "typical_lap")

    def __init__(self, pace, fuel_per_lap, compound_offset, wear_per_lap,
                 lap_noise, typical_lap) -> None:
        #: Driver → fitted pace term, seconds (absolute, includes the intercept).
        self.pace: Dict[str, float] = pace
        self.fuel_per_lap: float = fuel_per_lap
        #: Compound → offset in seconds, relative to the race's first compound
        #: alphabetically (whichever it is, it reads 0.0).
        self.compound_offset: Dict[str, float] = compound_offset
        #: Compound → seconds lost per lap of tyre age.
        self.wear_per_lap: Dict[str, float] = wear_per_lap
        #: Robust spread of a clean lap around the model, seconds.
        self.lap_noise: float = lap_noise
        self.typical_lap: float = typical_lap

    def gaps(self) -> Dict[str, float]:
        best = min(self.pace.values())
        return {
            driver: min(max((value - best) / self.typical_lap, 0.0), MAX_PLAUSIBLE_GAP_PCT)
            for driver, value in self.pace.items()
        }


def _fit(laps: Sequence[RaceLap], ages: Dict[Tuple[str, int], int]):
    drivers = sorted({lap.driver for lap in laps})
    compounds = sorted({lap.compound.upper() for lap in laps})
    d_index = {d: i for i, d in enumerate(drivers)}
    c_index = {c: i for i, c in enumerate(compounds)}
    n_d, n_c = len(drivers), len(compounds)
    # Columns: one per driver (absorbs the intercept), fuel (lap number), one
    # offset per compound after the first, one wear slope per compound.
    design = np.zeros((len(laps), n_d + 1 + (n_c - 1) + n_c))
    target = np.empty(len(laps))
    for row, lap in enumerate(laps):
        compound = c_index[lap.compound.upper()]
        design[row, d_index[lap.driver]] = 1.0
        design[row, n_d] = lap.lap
        if compound > 0:
            design[row, n_d + compound] = 1.0
        design[row, n_d + n_c + compound] = ages[(lap.driver, lap.lap)]
        target[row] = lap.lap_time_seconds
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    residuals = target - design @ coefficients
    return drivers, compounds, coefficients, residuals


def _robust_spread(residuals: np.ndarray) -> float:
    return 1.4826 * float(np.median(np.abs(residuals - np.median(residuals))))


def fit_race(laps: Sequence[RaceLap]) -> Optional[RaceFit]:
    """The race's lap-time model, or None without enough clean running to
    say anything — a wet race behind the safety car, or a data gap."""
    clean = _clean_laps(laps)
    counts: Dict[str, int] = {}
    for lap in clean:
        counts[lap.driver] = counts.get(lap.driver, 0) + 1
    clean = [lap for lap in clean if counts[lap.driver] >= MIN_CLEAN_LAPS_PER_DRIVER]
    if len({lap.driver for lap in clean}) < MIN_DRIVERS:
        return None

    drivers, compounds, coefficients, residuals = _fit(clean, _tyre_age(clean))
    # Refit without the laps the model cannot explain — traffic, a lock-up, a
    # flag the field median did not catch.
    spread = _robust_spread(residuals)
    if spread > 0:
        kept = [lap for lap, r in zip(clean, residuals) if abs(r) <= RESIDUAL_CUTOFF * spread]
        if len({lap.driver for lap in kept}) == len(drivers):
            drivers, compounds, coefficients, residuals = _fit(kept, _tyre_age(kept))

    n_d, n_c = len(drivers), len(compounds)
    return RaceFit(
        pace=dict(zip(drivers, coefficients[:n_d].tolist())),
        fuel_per_lap=float(coefficients[n_d]),
        compound_offset=dict(zip(compounds, [0.0] + coefficients[n_d + 1:n_d + n_c].tolist())),
        wear_per_lap=dict(zip(compounds, coefficients[n_d + n_c:].tolist())),
        lap_noise=_robust_spread(residuals),
        typical_lap=float(np.median([lap.lap_time_seconds for lap in clean])),
    )


def race_gaps(laps: Sequence[RaceLap]) -> Dict[str, float]:
    """Each driver's corrected pace deficit to the fastest, for one race.

    Empty when the race does not have enough clean running to say anything.
    """
    fit = fit_race(laps)
    return fit.gaps() if fit is not None else {}


def gaps_by_race(
    laps: Iterable[RaceLap],
) -> Dict[Tuple[int, int], Dict[str, float]]:
    """Corrected pace gaps for every race, keyed by (season, round)."""
    by_race: Dict[Tuple[int, int], List[RaceLap]] = {}
    for lap in laps:
        by_race.setdefault((lap.season, lap.round), []).append(lap)
    out = {}
    for key, race in by_race.items():
        gaps = race_gaps(race)
        if gaps:
            out[key] = gaps
    return out


def _before(key: Tuple[int, int], season: int, target_round: int) -> bool:
    race_season, race_round = key
    return race_season < season or (race_season == season and race_round < target_round)


def recent_gap(
    gaps: Dict[Tuple[int, int], Dict[str, float]],
    driver: str,
    season: int,
    target_round: int,
    window: int = PACE_FORM_WINDOW,
) -> float:
    """Mean corrected race-pace deficit over the races before ``target_round``."""
    history = sorted(
        (key, by_driver[driver]) for key, by_driver in gaps.items()
        if _before(key, season, target_round) and driver in by_driver
    )
    if not history:
        return NEUTRAL_GAP_PCT
    recent = [value for _, value in history[-window:]]
    return sum(recent) / len(recent)


def team_recent_gap(
    gaps: Dict[Tuple[int, int], Dict[str, float]],
    teams: Dict[Tuple[int, int], Dict[str, str]],
    team: str,
    season: int,
    target_round: int,
    window: int = PACE_FORM_WINDOW,
) -> float:
    """The car's recent race pace: its better driver each race, averaged.

    ``teams`` maps each race to its driver → team entries (from results), so a
    driver who changed seats counts for the car they drove that day. Matched on
    organisation, so a rename does not reset the car's record.
    """
    lineage = team_lineage.lineage_of(team)
    history = []
    for key, by_driver in gaps.items():
        if not _before(key, season, target_round):
            continue
        entries = teams.get(key, {})
        values = [
            gap for driver, gap in by_driver.items()
            if team_lineage.lineage_of(entries.get(driver, "")) == lineage
        ]
        if values:
            history.append((key, min(values)))
    if not history:
        return NEUTRAL_GAP_PCT
    history.sort()
    recent = [value for _, value in history[-window:]]
    return sum(recent) / len(recent)


def teams_by_race(results: Iterable) -> Dict[Tuple[int, int], Dict[str, str]]:
    """(season, round) → {driver: team}, from race results."""
    out: Dict[Tuple[int, int], Dict[str, str]] = {}
    for row in results:
        out.setdefault((row.season, row.round), {})[row.driver] = row.team
    return out


def restrict(
    gaps: Optional[Dict[Tuple[int, int], Dict[str, float]]],
    season: int,
    target_round: int,
) -> Dict[Tuple[int, int], Dict[str, float]]:
    """Only races strictly before the target — the same cut results get."""
    return {k: v for k, v in (gaps or {}).items() if _before(k, season, target_round)}
