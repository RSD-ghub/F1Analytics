"""Qualifying pace as a feature — the richest signal results data alone omits.

Grid position is an ordinal. It says a driver was third, not whether third was
five hundredths off pole or nine tenths. Those describe completely different
races and the ordinal collapses them into the same number, which is why adding
the *times* is worth more than any amount of extra history.

**Gaps are normalised by pole time, never measured in seconds.** A 0.5s deficit
around Monaco (~70s) is proportionally far larger than the same 0.5s at Spa
(~105s). Raw seconds would make identical performances look different by circuit
and make the fitted weight an average over track lengths rather than over
performance.

Two features come out of this, and they serve different windows:

* ``quali_gap_pct`` — this weekend's gap. Post-quali only; it is precisely the
  information that window exists to exploit.
* ``recent_quali_gap_pct`` — the rolling mean of prior weekends' gaps. Available
  **before** qualifying runs, and therefore usable pre-quali. It is a read on
  car pace rather than on one lap, and it is the most promising thing we can
  give the weaker of the two models.
"""

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.services.ingestion_client import QualiResult

#: Stand-in when a driver has no qualifying history. Roughly a midfield deficit;
#: zero would read as "on pole", which is the opposite of "we do not know".
NEUTRAL_GAP_PCT = 0.015

#: How many prior weekends count toward rolling qualifying pace. Matches the
#: race form window so the two features describe the same span of time.
QUALI_FORM_WINDOW = 5

#: Beyond this, a "gap" is a broken time rather than a slow lap — an aborted
#: run, a red-flagged session, or a unit mismatch. Clamped so one bad row cannot
#: drag a driver's rolling average into nonsense.
MAX_PLAUSIBLE_GAP_PCT = 0.15


def gap_to_pole(rows: Sequence[QualiResult]) -> Dict[str, float]:
    """Fractional deficit to the session's fastest time, per driver.

    Returns an empty mapping when no usable time exists, so callers fall back to
    the neutral value rather than dividing by zero.
    """
    timed = [(row.driver, row.best_seconds) for row in rows if row.best_seconds > 0]
    if not timed:
        return {}

    pole = min(seconds for _, seconds in timed)
    if pole <= 0:
        return {}

    gaps: Dict[str, float] = {}
    for driver, seconds in timed:
        gap = (seconds - pole) / pole
        gaps[driver] = min(max(gap, 0.0), MAX_PLAUSIBLE_GAP_PCT)
    return gaps


def gaps_by_race(
    rows: Iterable[QualiResult],
) -> Dict[Tuple[int, int], Dict[str, float]]:
    """Gap-to-pole for every driver in every session, keyed by (season, round)."""
    by_race: Dict[Tuple[int, int], List[QualiResult]] = {}
    for row in rows:
        by_race.setdefault((row.season, row.round), []).append(row)
    return {key: gap_to_pole(session) for key, session in by_race.items()}


def recent_gap(
    gaps: Dict[Tuple[int, int], Dict[str, float]],
    driver: str,
    season: int,
    target_round: int,
    window: int = QUALI_FORM_WINDOW,
) -> float:
    """Mean qualifying deficit over the sessions before ``target_round``.

    The point-in-time cut applies to qualifying exactly as it does to results:
    only sessions strictly before the target round, plus prior seasons. Using
    this weekend's session here would leak the very thing the pre-quali window
    is defined by not knowing.
    """
    history: List[Tuple[Tuple[int, int], float]] = []
    for (race_season, race_round), by_driver in gaps.items():
        if race_season > season:
            continue
        if race_season == season and race_round >= target_round:
            continue
        if driver in by_driver:
            history.append(((race_season, race_round), by_driver[driver]))

    if not history:
        return NEUTRAL_GAP_PCT

    history.sort(key=lambda item: item[0])
    recent = [value for _, value in history[-window:]]
    return sum(recent) / len(recent)


def current_gap(
    gaps: Dict[Tuple[int, int], Dict[str, float]],
    driver: str,
    season: int,
    target_round: int,
) -> float:
    """This weekend's qualifying deficit. Post-quali only."""
    session = gaps.get((season, target_round), {})
    return session.get(driver, NEUTRAL_GAP_PCT)


def teammate_gap(
    gaps: Dict[Tuple[int, int], Dict[str, float]],
    quali_rows: Sequence[QualiResult],
    driver: str,
    team: str,
    season: int,
    target_round: int,
    window: int = QUALI_FORM_WINDOW,
) -> float:
    """Rolling qualifying deficit to the team-mate. Negative is better.

    Qualifying is the cleanest driver comparison in the sport: same car, same
    fuel, one lap, no strategy or traffic to muddy it. The gap between team-mates
    over a run of sessions is close to a direct read on the driver rather than
    the machinery.
    """
    by_race: Dict[Tuple[int, int], List[QualiResult]] = {}
    for row in quali_rows:
        if row.team != team:
            continue
        if row.season > season or (row.season == season and row.round >= target_round):
            continue
        by_race.setdefault((row.season, row.round), []).append(row)

    deltas: List[Tuple[Tuple[int, int], float]] = []
    for key in sorted(by_race):
        session_gaps = gaps.get(key, {})
        mine = session_gaps.get(driver)
        others = [
            session_gaps[row.driver]
            for row in by_race[key]
            if row.driver != driver and row.driver in session_gaps
        ]
        if mine is None or not others:
            continue
        deltas.append((key, mine - (sum(others) / len(others))))

    if not deltas:
        return 0.0  # no comparison available is not an advantage either way
    recent = [value for _, value in deltas[-window:]]
    return sum(recent) / len(recent)
