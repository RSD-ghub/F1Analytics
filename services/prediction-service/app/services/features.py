"""Point-in-time feature construction.

The entire leakage defence is one filter, applied once, at the top of
``build_snapshot``: everything at or after the target round is removed before any
feature sees the data. Every downstream function receives only ``history`` and has
no access to the full result set, so a leaky feature cannot be written by
accident — it would have to reach for data that is not in scope.

This matters more than any other design decision in the service. A model fed
post-race data scores beautifully in backtests and is worthless live, and nothing
in the output reveals the difference. ``tests/test_leakage.py`` enforces the
invariant directly: rebuild the features with all at-or-after data physically
deleted and assert the result is identical.

Prior *seasons* are fully available — a driver's career record and circuit
history are legitimately known before the race. Only the current season is cut at
the target round.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from app.models.schemas import DriverFeatures, FeatureSnapshot, LockWindow
from app.services import practice_signal, quali_pace
from app.services.ingestion_client import (
    GridSlot,
    PracticePace,
    QualiResult,
    RaceResult,
)

#: How many recent races count as "form". Five is roughly a month of racing —
#: long enough to survive one bad weekend, short enough to track a car upgrade.
FORM_WINDOW = 5

#: Used where a driver has no history to average. Deliberately mid-field rather
#: than optimistic: an unknown driver is not a likely winner.
NEUTRAL_POSITION = 12.0


def build_snapshot(
    results: Sequence[RaceResult],
    season: int,
    target_round: int,
    window: LockWindow,
    circuit: str = "",
    grid: Optional[Sequence[GridSlot]] = None,
    quali_history: Optional[Sequence[QualiResult]] = None,
    practice: Optional[Sequence[PracticePace]] = None,
) -> FeatureSnapshot:
    """Build the feature set for one forecast.

    ``grid`` is accepted only for the post-quali window; passing it pre-quali is
    ignored rather than trusted, because a pre-quali forecast that saw the grid
    would be mislabelled in the track record and would flatter the model exactly
    where it claims to be doing something hard.
    """
    history = _point_in_time(results, season, target_round)
    use_grid = window is LockWindow.POST_QUALI and grid

    # Qualifying pace. The rolling features are cut at the target round exactly
    # as results are; only the post-quali window may see this weekend's session.
    quali_rows = list(quali_history or [])
    quali_gaps = quali_pace.gaps_by_race(quali_rows)

    # Practice for *this* round is fair game in both windows: it runs before
    # qualifying, so it leaks nothing a pre-quali forecast would not have.
    practice_gaps = practice_signal.gaps_for_round(
        practice or [], season, target_round
    )

    field = _entry_list(history, season, grid if use_grid else None)
    # `effective` prefers the confirmed grid and falls back to qualifying
    # classification; `build_snapshot` does not decide whether that fallback is
    # acceptable — the predictor flags it on the prediction.
    grid_by_driver = (
        {slot.driver: slot.effective for slot in grid} if use_grid else {}
    )

    drivers = [
        _driver_features(
            driver=driver,
            team=team,
            history=history,
            season=season,
            circuit=circuit,
            grid_position=grid_by_driver.get(driver, 0),
            quali_gaps=quali_gaps,
            quali_rows=quali_rows,
            target_round=target_round,
            use_current_quali=bool(use_grid),
            practice_gap=practice_gaps.get(
                driver, practice_signal.NEUTRAL_GAP_PCT
            ),
        )
        # Sorted so the snapshot is byte-stable: an unstable order would make
        # the snapshot hash change without the data changing.
        for driver, team in sorted(field.items())
    ]

    included = sorted({row.round for row in history if row.season == season})
    as_of = max(included) if included else 0

    snapshot = FeatureSnapshot(
        snapshot_id="",
        season=season,
        round=target_round,
        window=window,
        as_of_round=as_of,
        included_rounds=included,
        created_at=datetime.now(timezone.utc),
        drivers=drivers,
    )
    snapshot.snapshot_id = snapshot_hash(snapshot)
    return snapshot


def _point_in_time(
    results: Sequence[RaceResult], season: int, target_round: int
) -> List[RaceResult]:
    """The cut. Nothing downstream sees anything this removes."""
    return [
        row
        for row in results
        if row.season < season or (row.season == season and row.round < target_round)
    ]


def _entry_list(
    history: Sequence[RaceResult],
    season: int,
    grid: Optional[Sequence[GridSlot]],
) -> Dict[str, str]:
    """Who is racing, as best we can know before the race.

    Post-quali the grid is authoritative — it is the actual entry list. Pre-quali
    the best available proxy is whoever raced most recently, which handles
    mid-season driver changes without needing a separate entry-list feed.
    """
    if grid:
        return {slot.driver: slot.team for slot in grid}

    this_season = [row for row in history if row.season == season]
    if not this_season:
        # Round 1 with no prior season data at all.
        recent = sorted(history, key=lambda r: (r.season, r.round), reverse=True)
        latest_round = (
            (recent[0].season, recent[0].round) if recent else None
        )
        if latest_round is None:
            return {}
        return {
            row.driver: row.team
            for row in history
            if (row.season, row.round) == latest_round
        }

    last_round = max(row.round for row in this_season)
    return {
        row.driver: row.team for row in this_season if row.round == last_round
    }


def _driver_features(
    driver: str,
    team: str,
    history: Sequence[RaceResult],
    season: int,
    circuit: str,
    grid_position: int,
    quali_gaps: Optional[dict] = None,
    quali_rows: Optional[Sequence[QualiResult]] = None,
    target_round: int = 0,
    use_current_quali: bool = False,
    practice_gap: float = practice_signal.NEUTRAL_GAP_PCT,
) -> DriverFeatures:
    quali_gaps = quali_gaps or {}
    quali_rows = quali_rows or []

    recent_quali = quali_pace.recent_gap(quali_gaps, driver, season, target_round)
    teammate_quali = quali_pace.teammate_gap(
        quali_gaps, quali_rows, driver, team, season, target_round
    )
    current_quali = (
        quali_pace.current_gap(quali_gaps, driver, season, target_round)
        if use_current_quali
        else 0.0
    )

    mine = [row for row in history if row.driver == driver]
    if not mine:
        return DriverFeatures(
            driver=driver,
            team=team,
            avg_finish_recent=NEUTRAL_POSITION,
            avg_finish_season=NEUTRAL_POSITION,
            team_avg_finish=_team_average(history, team, season),
            circuit_avg_finish=NEUTRAL_POSITION,
            avg_grid_recent=NEUTRAL_POSITION,
            recent_quali_gap_pct=recent_quali,
            quali_teammate_gap_pct=teammate_quali,
            quali_gap_pct=current_quali,
            practice_long_run_gap_pct=practice_gap,
            grid_position=grid_position,
            is_cold_start=True,
        )

    ordered = sorted(mine, key=lambda r: (r.season, r.round))
    recent = ordered[-FORM_WINDOW:]
    this_season = [row for row in ordered if row.season == season]

    return DriverFeatures(
        recent_quali_gap_pct=recent_quali,
        quali_teammate_gap_pct=teammate_quali,
        quali_gap_pct=current_quali,
        practice_long_run_gap_pct=practice_gap,
        avg_grid_recent=_average_grid(recent),
        teammate_delta=_teammate_delta(history, driver, team, season),
        positions_gained=_positions_gained(recent),
        driver=driver,
        team=team,
        avg_finish_recent=_average_finish(recent),
        avg_finish_season=_average_finish(this_season),
        points_per_race=(
            sum(row.points for row in this_season) / len(this_season)
            if this_season
            else 0.0
        ),
        dnf_rate=sum(1 for row in ordered if row.retired) / len(ordered),
        team_avg_finish=_team_average(history, team, season),
        circuit_avg_finish=(
            _average_finish([row for row in ordered if row.circuit == circuit])
            if circuit
            else NEUTRAL_POSITION
        ),
        races_completed=len(ordered),
        grid_position=grid_position,
        is_cold_start=False,
    )


def _average_grid(rows: Sequence[RaceResult]) -> float:
    """Recent qualifying pace.

    Zero marks a pit-lane start or unknown grid slot rather than pole, so those
    rows are excluded instead of being read as the best possible qualifying.
    """
    slots = [row.grid_position for row in rows if row.grid_position > 0]
    if not slots:
        return NEUTRAL_POSITION
    return sum(slots) / len(slots)


def _positions_gained(rows: Sequence[RaceResult]) -> float:
    """Mean grid-to-flag improvement over recent races.

    Only classified finishes count: a retirement's finishing position is not a
    racing outcome, and counting it would read a blown engine as poor racecraft.
    """
    deltas = [
        row.grid_position - row.position
        for row in rows
        if row.classified and row.grid_position > 0
    ]
    if not deltas:
        return 0.0
    return sum(deltas) / len(deltas)


def _teammate_delta(
    history: Sequence[RaceResult], driver: str, team: str, season: int
) -> float:
    """How far ahead of their team-mate this driver has been finishing.

    Negative is better — the same convention as every other position feature.
    Team-mates share the car, so the gap between them is the least
    car-contaminated driver signal available from results alone.

    Returns 0.0 when there is no team-mate to compare against (a one-car entry,
    or a mid-season replacement with no shared races yet), which correctly says
    "no information" rather than inventing an advantage in either direction.
    """
    same_team = [
        row
        for row in history
        if row.team == team and row.season == season and row.classified
    ]
    mine = [row.position for row in same_team if row.driver == driver]
    theirs = [row.position for row in same_team if row.driver != driver]
    if not mine or not theirs:
        return 0.0
    return (sum(mine) / len(mine)) - (sum(theirs) / len(theirs))


def _average_finish(rows: Sequence[RaceResult]) -> float:
    """Mean finishing position over classified results only.

    Unclassified entries are excluded rather than counted as a bad finish. A
    retirement says something about reliability, which ``dnf_rate`` carries, and
    folding it into pace would double-count it — and worse, would read a
    first-lap retirement as a genuine 19th place, which is how a driver winning
    four of five races ends up with a mid-field pace rating.
    """
    finished = [row.position for row in rows if row.classified]
    if not finished:
        return NEUTRAL_POSITION
    return sum(finished) / len(finished)


def _team_average(
    history: Sequence[RaceResult], team: str, season: int
) -> float:
    rows = [row for row in history if row.team == team and row.season == season]
    if not rows:
        rows = [row for row in history if row.team == team]
    return _average_finish(rows)


def snapshot_hash(snapshot: FeatureSnapshot) -> str:
    """Content hash over the feature values.

    Identifies a snapshot by what it contains rather than when it was made, so
    the reproducibility test can assert that rebuilding produced the same
    features without comparing timestamps. ``created_at`` and the id itself are
    excluded for that reason.
    """
    payload = {
        "season": snapshot.season,
        "round": snapshot.round,
        "window": snapshot.window.value,
        "as_of_round": snapshot.as_of_round,
        "included_rounds": snapshot.included_rounds,
        "drivers": [
            driver.model_dump() for driver in snapshot.drivers
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
