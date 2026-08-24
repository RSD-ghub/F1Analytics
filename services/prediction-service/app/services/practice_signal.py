"""Practice long-run pace as a point-in-time feature.

The one current-weekend pace signal both lock windows may use. Free practice
runs *before* qualifying, so using this weekend's FP2 in a pre-quali forecast
leaks nothing — unlike the grid, which the pre-quali window must not see.

That makes it the most promising input available for the weaker model: it is
measured after upgrades are bolted on, and it is what news about upgrades only
gossips about.
"""

from typing import Dict, Iterable, Sequence, Tuple

from app.services.ingestion_client import PracticePace

#: Stand-in when a weekend has no usable practice pace — a washed-out session,
#: or a season before the data exists. Mid-pack rather than zero, which would
#: read as "fastest car in the session".
NEUTRAL_GAP_PCT = 0.008

#: FP2 first, not FP3. This is a domain fact rather than an ordering
#: convenience: FP2 is the traditional race-simulation session — on most
#: weekends it runs at the same time of day as the race, and teams use it for
#: long, high-fuel stints. FP3 is Saturday morning and is overwhelmingly
#: qualifying preparation on low fuel, which is the wrong thing to measure when
#: the feature is explicitly about race pace.
#:
#: FP1 last: it is the least representative, often given over to rookie runs
#: and aero rakes.
SESSION_PREFERENCE = (
    "FP2", "Practice 2",
    "FP3", "Practice 3",
    "FP1", "Practice 1",
)

MAX_PLAUSIBLE_GAP_PCT = 0.10


def gaps_for_round(
    rows: Iterable[PracticePace], season: int, round_number: int
) -> Dict[str, float]:
    """Long-run deficit to the session best, for one weekend.

    Picks a single session rather than averaging across them: FP1 and FP3 are
    run on different fuel loads, tyres and track conditions, so pooling their
    lap times compares drivers on incomparable runs.
    """
    weekend = [
        row
        for row in rows
        if row.season == season
        and row.round == round_number
        and row.long_run_seconds > 0
        and row.long_run_laps > 0
    ]
    if not weekend:
        return {}

    by_session: Dict[str, list] = {}
    for row in weekend:
        by_session.setdefault(row.session_name, []).append(row)

    chosen = None
    for name in SESSION_PREFERENCE:
        if name in by_session:
            chosen = by_session[name]
            break
    if chosen is None:
        # Unrecognised naming — fall back to whichever session had most runners.
        chosen = max(by_session.values(), key=len)

    reference = min(row.long_run_seconds for row in chosen)
    if reference <= 0:
        return {}
    return {
        row.driver: min(
            max((row.long_run_seconds - reference) / reference, 0.0),
            MAX_PLAUSIBLE_GAP_PCT,
        )
        for row in chosen
    }
