"""Long-run pace from practice sessions.

**Why this and not news about upgrades.** Practice runs *after* a team bolts on
an upgrade and *before* qualifying. It is therefore the only signal that
measures — objectively, in seconds — whether a change actually worked, at a
moment both lock windows are allowed to see. News says "Ferrari brought a new
floor"; FP2 says "Ferrari is three tenths a lap quicker over a stint".

**Long-run pace, not the headline time.** A single-lap best is dominated by fuel
load and engine mode, which teams vary deliberately to disguise their hand. A
median over a sustained stint is far harder to fake and far closer to race pace.

**The filtering is the whole job.** Practice laps are mostly noise: out-laps,
in-laps, aborted runs, traffic, practice starts. A naive mean over every lap in
FP2 measures how much time a team spent in the pits.
"""

import logging
import statistics
from typing import Dict, List, Optional, Sequence

import pandas as pd

from app.models.schemas import ExpectedSession, PracticePaceRow
from app.services.transforms import safe_int, safe_text, stable_id, to_seconds

logger = logging.getLogger(__name__)

#: Consecutive timed laps needed before a run counts as a "long run". Shorter
#: bursts are qualifying simulations on low fuel, which measure something else.
MIN_STINT_LAPS = 4

#: Within a stint, a lap slower than this multiple of that stint's own median is
#: traffic or a lift, not pace.
#:
#: Measured against the *stint* median, never the driver's session best. That
#: was the original implementation and it was badly wrong: a high-fuel long run
#: legitimately sits 5-7% off a low-fuel qualifying simulation, so filtering
#: against the session best threw away most of every genuine long run and kept
#: only its quickest laps. The visible symptom was a driver's "long run" being
#: reported over three laps and coming out fastest in the session.
OUTLIER_MULTIPLE = 1.03


def extract_practice_pace(
    session: ExpectedSession,
    session_name: str,
    laps_df: Optional[pd.DataFrame],
    driver_names: Dict[str, str],
) -> List[PracticePaceRow]:
    """Reduce a practice session's laps to one pace row per driver."""
    rows: List[PracticePaceRow] = []
    if laps_df is None or laps_df.empty:
        return rows
    for column in ("Driver", "LapNumber", "LapTime"):
        if column not in laps_df:
            return rows

    for code, driver_laps in laps_df.groupby("Driver"):
        driver = driver_names.get(safe_text(code), safe_text(code))
        if not driver:
            continue
        timed = _timed_laps(driver_laps)
        if not timed:
            continue

        best = min(t for _, t in timed)
        long_run = _long_run_median(timed, best)
        rows.append(
            PracticePaceRow(
                id=stable_id("practice", session.season, session.round,
                             session_name, driver),
                season=session.season,
                round=session.round,
                race_name=session.race_name,
                session_name=session_name,
                driver=driver,
                best_lap_seconds=best,
                long_run_seconds=long_run or 0.0,
                long_run_laps=_long_run_lap_count(timed, best),
                timed_laps=len(timed),
            )
        )
    return rows


def _timed_laps(driver_laps: pd.DataFrame) -> List[tuple]:
    """(lap_number, seconds) for laps that were actually raced.

    A null ``LapTime`` is FastF1's marker for an out-lap, in-lap or otherwise
    invalid lap — exactly the ones that must not reach a pace median.
    """
    out: List[tuple] = []
    for _, lap in driver_laps.iterrows():
        seconds = to_seconds(lap.get("LapTime"))
        if seconds <= 0:
            continue
        if pd.notna(lap.get("PitInTime")) or pd.notna(lap.get("PitOutTime")):
            continue
        out.append((safe_int(lap.get("LapNumber"), 0), seconds))
    out.sort()
    return out


def _runs(timed: Sequence[tuple]) -> List[List[float]]:
    """Split laps into consecutive runs, breaking wherever a lap is missing.

    A gap in lap numbers means the car pitted or the run was abandoned, so the
    laps either side are not part of one sustained stint.
    """
    runs: List[List[float]] = []
    current: List[float] = []
    previous: Optional[int] = None
    for lap_number, seconds in timed:
        if previous is not None and lap_number != previous + 1:
            runs.append(current)
            current = []
        current.append(seconds)
        previous = lap_number
    if current:
        runs.append(current)
    return runs


def _long_run_laps(timed: Sequence[tuple], best: float) -> List[float]:
    """Laps belonging to sustained runs, with in-stint outliers removed.

    ``best`` is accepted but deliberately unused for filtering — see
    ``OUTLIER_MULTIPLE``. Each stint is judged against itself.
    """
    keep: List[float] = []
    for run in _runs(timed):
        if len(run) < MIN_STINT_LAPS:
            continue
        reference = statistics.median(run)
        clean = [lap for lap in run if lap <= reference * OUTLIER_MULTIPLE]
        # A stint that loses most of its laps to the filter was not a clean run.
        if len(clean) >= MIN_STINT_LAPS:
            keep.extend(clean)
    return keep


def _long_run_median(timed: Sequence[tuple], best: float) -> Optional[float]:
    laps = _long_run_laps(timed, best)
    return statistics.median(laps) if laps else None


def _long_run_lap_count(timed: Sequence[tuple], best: float) -> int:
    return len(_long_run_laps(timed, best))


def gap_to_best(rows: Sequence[PracticePaceRow], use_long_run: bool = True) -> Dict[str, float]:
    """Fractional deficit to the session's best, per driver.

    Normalised by the reference time for the same reason qualifying gaps are:
    half a second means something different at Monaco than at Spa.
    """
    def value(row: PracticePaceRow) -> float:
        return row.long_run_seconds if use_long_run else row.best_lap_seconds

    timed = [(r.driver, value(r)) for r in rows if value(r) > 0]
    if not timed:
        return {}
    reference = min(seconds for _, seconds in timed)
    if reference <= 0:
        return {}
    return {
        driver: min(max((seconds - reference) / reference, 0.0), 0.15)
        for driver, seconds in timed
    }
