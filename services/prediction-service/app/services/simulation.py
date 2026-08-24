"""Championship Monte Carlo.

The question "who wins the title?" has no closed-form answer — it depends on a
dozen more races, each with its own distribution over outcomes. So it is answered
by brute force: simulate the rest of the season many thousands of times and count
how often each driver ends up on top. Run it 20,000 times and a driver who wins
the title in 6,200 of them has a 31% title probability.

The per-race distribution comes from the same ``RaceModel`` that produces race
forecasts, so the championship view can never disagree with the race view — a
common failure mode when the two are computed separately.

**Both are approximations, in opposite directions.** Features are frozen at
today's values, so a mid-season upgrade that transforms a car is not anticipated;
and every remaining race is simulated independently, so a genuinely unreliable
car does not get correlated failures. The honest framing is "if the season
continued as it looks today", and the API says so rather than implying more.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Sequence

import numpy as np

from app.models.schemas import (
    ChampionshipForecast,
    ChampionshipOutcome,
    DriverFeatures,
)
from app.services.ingestion_client import RaceResult
from app.services.model import RaceModel

logger = logging.getLogger(__name__)

#: Points for positions 1-10 under the current system. Simulation is
#: forward-looking, so only today's scale applies — historical points come from
#: the ingested results, which carry whatever system was in force at the time.
POINTS_BY_POSITION = [25.0, 18.0, 15.0, 12.0, 10.0, 8.0, 6.0, 4.0, 2.0, 1.0]


def points_for(position: int) -> float:
    if 1 <= position <= len(POINTS_BY_POSITION):
        return POINTS_BY_POSITION[position - 1]
    return 0.0


def _points_table(field_size: int) -> np.ndarray:
    """Position → points, indexed from 0, for vectorised lookup."""
    return np.array(
        [points_for(position) for position in range(1, field_size + 1)], dtype=float
    )


def simulate_championship(
    model: RaceModel,
    drivers: Sequence[DriverFeatures],
    current_points: Dict[str, float],
    remaining_rounds: Sequence[int],
    season: int,
    as_of_round: int,
    runs: int = 20000,
    seed: int = 12345,
) -> ChampionshipForecast:
    """Title probabilities from simulating every remaining race."""
    if not drivers:
        return ChampionshipForecast(
            season=season,
            as_of_round=as_of_round,
            remaining_rounds=list(remaining_rounds),
            runs=0,
            seed=seed,
            model_version=model.version,
            generated_at=datetime.now(timezone.utc),
        )

    names = [driver.driver for driver in drivers]
    teams = {driver.driver: driver.team for driver in drivers}
    starting = np.array([current_points.get(name, 0.0) for name in names])
    table = _points_table(len(drivers))

    totals = np.tile(starting, (runs, 1))
    for offset, _round in enumerate(remaining_rounds):
        # A distinct seed per race keeps races independent while leaving the
        # whole simulation reproducible from the single top-level seed.
        positions = model.sample_orders(drivers, seed=seed + offset, runs=runs)
        totals += table[positions - 1]

    return _summarise(
        names=names,
        teams=teams,
        totals=totals,
        starting=starting,
        season=season,
        as_of_round=as_of_round,
        remaining_rounds=list(remaining_rounds),
        runs=runs,
        seed=seed,
        model_version=model.version,
    )


def _summarise(
    names, teams, totals, starting, season, as_of_round,
    remaining_rounds, runs, seed, model_version,
) -> ChampionshipForecast:
    # Ties on points are broken by countback in reality; here the first index
    # wins, which is close enough given exact ties are vanishingly rare across
    # a full remaining season.
    champion_index = totals.argmax(axis=1)
    champion_counts = np.bincount(champion_index, minlength=len(names))
    p_champion = champion_counts / runs

    ranks = (-totals).argsort(axis=1).argsort(axis=1) + 1
    p_top_three = (ranks <= 3).sum(axis=0) / runs
    expected_points = totals.mean(axis=0)

    drivers = [
        ChampionshipOutcome(
            driver=name,
            team=teams.get(name, "Unknown"),
            current_points=float(starting[index]),
            p_champion=float(p_champion[index]),
            expected_final_points=float(expected_points[index]),
            p_top_three=float(p_top_three[index]),
        )
        for index, name in enumerate(names)
    ]
    drivers.sort(key=lambda d: (-d.p_champion, -d.expected_final_points, d.driver))

    return ChampionshipForecast(
        season=season,
        as_of_round=as_of_round,
        remaining_rounds=remaining_rounds,
        runs=runs,
        seed=seed,
        model_version=model_version,
        generated_at=datetime.now(timezone.utc),
        drivers=drivers,
        constructors=_constructor_outcomes(drivers, teams, totals, names, starting, runs),
    )


def _constructor_outcomes(
    driver_outcomes, teams, totals, names, starting, runs
) -> List[ChampionshipOutcome]:
    """Aggregate driver simulations into constructor standings.

    Summed inside each iteration rather than across the marginals, so a team's
    two cars stay correlated within a simulated season — adding marginal
    probabilities would badly misstate a team's title chances.
    """
    by_team: Dict[str, List[int]] = {}
    for index, name in enumerate(names):
        by_team.setdefault(teams.get(name, "Unknown"), []).append(index)

    team_names = sorted(by_team)
    team_totals = np.stack(
        [totals[:, by_team[team]].sum(axis=1) for team in team_names], axis=1
    )
    team_start = np.array(
        [float(starting[by_team[team]].sum()) for team in team_names]
    )

    champion_counts = np.bincount(
        team_totals.argmax(axis=1), minlength=len(team_names)
    )
    ranks = (-team_totals).argsort(axis=1).argsort(axis=1) + 1

    outcomes = [
        ChampionshipOutcome(
            driver=team,
            team=team,
            current_points=float(team_start[index]),
            p_champion=float(champion_counts[index] / runs),
            expected_final_points=float(team_totals[:, index].mean()),
            p_top_three=float((ranks[:, index] <= 3).sum() / runs),
        )
        for index, team in enumerate(team_names)
    ]
    outcomes.sort(key=lambda t: (-t.p_champion, -t.expected_final_points, t.driver))
    return outcomes


def current_points_from(results: Sequence[RaceResult], season: int) -> Dict[str, float]:
    """Points scored so far this season, per driver."""
    table: Dict[str, float] = {}
    for row in results:
        if row.season != season:
            continue
        table[row.driver] = table.get(row.driver, 0.0) + row.points
    return table
