"""Championship standings, computed point-in-time.

Standings are the single most leakage-prone feature in the system. "Verstappen's
championship lead" is only a legitimate input to a Round 12 forecast if it is the
lead *before Round 12 was run* — and the difference is invisible in the output.
A model fed post-race standings scores brilliantly in backtests and is worthless
live.

So the exclusive-bound call is the one with the explicit name
(``standings_before_round``) and the returned snapshot records exactly which
rounds it covers. A stored prediction can therefore be audited after the fact:
if a Round 12 forecast cites a snapshot covering rounds 1-12, that is provable
leakage rather than a matter of opinion.

Points are summed from the ingested ``points`` column rather than recomputed from
finishing positions, so the scoring-system changes across 2010-2026 (and the
2014 double-points experiment, and sprint points) need no special handling.
"""

from typing import Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

from app.models.schemas import ResultRow

PODIUM_POSITIONS = 3


class DriverStanding(BaseModel):
    position: int
    driver: str
    team: str = "Unknown"
    points: float = 0.0
    wins: int = 0
    podiums: int = 0
    races: int = 0


class ConstructorStanding(BaseModel):
    position: int
    team: str
    points: float = 0.0
    wins: int = 0
    races: int = 0


class StandingsSnapshot(BaseModel):
    """Standings plus the provenance needed to audit them for leakage."""

    season: int
    #: Highest round included. ``None`` means the season had no results yet.
    through_round: Optional[int] = None
    #: Every round actually counted. Explicit rather than a range, because a
    #: mid-season gap in ingested data changes what these standings mean.
    included_rounds: List[int] = Field(default_factory=list)
    drivers: List[DriverStanding] = Field(default_factory=list)
    constructors: List[ConstructorStanding] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.included_rounds


def standings_before_round(
    results: Iterable[ResultRow], season: int, round_number: int
) -> StandingsSnapshot:
    """Standings as they stood *going into* ``round_number``.

    This is the only variant a forecast for ``round_number`` may use. Round 1 of
    a season correctly yields an empty snapshot — there is genuinely nothing
    known yet, and inventing a prior from last season is a modelling decision
    that belongs in prediction-service, not a silent default here.
    """
    return compute_standings(results, season, through_round=round_number - 1)


def compute_standings(
    results: Iterable[ResultRow],
    season: int,
    through_round: Optional[int] = None,
) -> StandingsSnapshot:
    """Standings including every round up to and including ``through_round``.

    ``through_round=None`` includes everything available — the "current
    standings" display query. For anything feeding a prediction, use
    ``standings_before_round``.
    """
    relevant = [
        row
        for row in results
        if row.season == season
        and (through_round is None or row.round <= through_round)
    ]

    if not relevant:
        return StandingsSnapshot(season=season)

    drivers = _accumulate_drivers(relevant)
    constructors = _accumulate_constructors(relevant)
    included = sorted({row.round for row in relevant})

    return StandingsSnapshot(
        season=season,
        through_round=max(included),
        included_rounds=included,
        drivers=_rank_drivers(drivers),
        constructors=_rank_constructors(constructors),
    )


# ── Accumulation ─────────────────────────────────────────────────────────────


def _is_finish(row: ResultRow, cutoff: int) -> bool:
    """Positions are 1-based; the extractor uses 999 for unclassified entries."""
    return 1 <= row.position <= cutoff


def _accumulate_drivers(rows: List[ResultRow]) -> Dict[str, DriverStanding]:
    table: Dict[str, DriverStanding] = {}
    for row in rows:
        standing = table.get(row.driver)
        if standing is None:
            standing = DriverStanding(position=0, driver=row.driver, team=row.team)
            table[row.driver] = standing

        standing.points += row.points
        standing.races += 1
        if _is_finish(row, 1):
            standing.wins += 1
        if _is_finish(row, PODIUM_POSITIONS):
            standing.podiums += 1
        # Mid-season transfers happen; the latest team is the useful one to show.
        if row.team and row.team != "Unknown":
            standing.team = row.team
    return table


def _accumulate_constructors(rows: List[ResultRow]) -> Dict[str, ConstructorStanding]:
    table: Dict[str, ConstructorStanding] = {}
    for row in rows:
        standing = table.get(row.team)
        if standing is None:
            standing = ConstructorStanding(position=0, team=row.team)
            table[row.team] = standing

        standing.points += row.points
        standing.races += 1
        if _is_finish(row, 1):
            standing.wins += 1
    return table


# ── Ranking ──────────────────────────────────────────────────────────────────
#
# Real F1 breaks a points tie on count of best finishes (most wins, then most
# seconds, and so on). Wins-then-podiums approximates that and covers every tie
# that matters in practice; the final sort on name is there so identical records
# never produce a non-deterministic order, which would make stored predictions
# irreproducible.


def _rank_drivers(table: Dict[str, DriverStanding]) -> List[DriverStanding]:
    ordered = sorted(
        table.values(),
        key=lambda s: (-s.points, -s.wins, -s.podiums, s.driver),
    )
    for index, standing in enumerate(ordered, start=1):
        standing.position = index
    return ordered


def _rank_constructors(table: Dict[str, ConstructorStanding]) -> List[ConstructorStanding]:
    ordered = sorted(
        table.values(),
        key=lambda s: (-s.points, -s.wins, s.team),
    )
    for index, standing in enumerate(ordered, start=1):
        standing.position = index
    return ordered
