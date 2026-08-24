"""Integrity checks and gap accounting — the "no data missed" guarantee.

Two jobs:

1. **Per session** — decide whether what we ingested is actually whole, beyond
   "the fetch didn't throw". A session that returns two drivers instead of twenty
   raises no exception; only a content check catches it.
2. **Per season range** — diff the expected-session manifest against recorded
   outcomes so "what are we missing?" is a query rather than a hunt through logs.

The checks are deliberately calibrated to flag *truncation*, not to encode a
model of a normal race. Real races are weird — Spa 2021 ran three laps behind a
safety car, drivers retire on lap one — and a check that flags a weird-but-real
race trains everyone to ignore it.
"""

from typing import Dict, Iterable, List

from app.models.schemas import (
    CompletenessSummary,
    IngestDepth,
    IntegrityCheck,
    IntegrityReport,
    SessionIngestState,
    SessionPayload,
    SessionState,
)

# FastF1 exposes lap, telemetry and weather data only from 2018; earlier seasons
# are results-only. Demanding laps for 2010 would mark eight complete seasons
# PARTIAL forever, which would make the gap list meaningless.
LAP_DATA_FROM_SEASON = 2018

# A classified field far below this means the results table was truncated, not
# that the race was unusual — even 2021 Spa classified all twenty entrants.
MIN_CLASSIFIED_DRIVERS = 10


def lap_data_expected(season: int) -> bool:
    return season >= LAP_DATA_FROM_SEASON


def run_integrity_checks(
    payload: SessionPayload, depth: IngestDepth = IngestDepth.FULL
) -> IntegrityReport:
    """Assess whether an ingested session is coherent and whole *for its depth*.

    A results-only ingest is judged on its classification alone. Applying the
    lap checks to it would mark every modern session PARTIAL for data nobody
    asked it to fetch, and a gap list full of expected noise stops being read.
    """
    season = payload.session.season
    checks: List[IntegrityCheck] = [
        _check_results_present(payload),
        _check_field_size(payload),
    ]

    if depth is IngestDepth.FULL and lap_data_expected(season):
        checks.append(_check_laps_present(payload))
        checks.append(_check_every_driver_has_laps(payload))
        checks.append(_check_lap_numbering(payload))
        checks.append(_check_stint_laps_reconcile(payload))
        checks.append(_check_pit_stops_within_stints(payload))

    return IntegrityReport(checks=checks)


# ── Individual checks ────────────────────────────────────────────────────────


def _check_results_present(payload: SessionPayload) -> IntegrityCheck:
    count = len(payload.results)
    return IntegrityCheck(
        name="results_present",
        passed=count > 0,
        detail="{} classified rows".format(count),
    )


def _check_field_size(payload: SessionPayload) -> IntegrityCheck:
    count = len(payload.results)
    return IntegrityCheck(
        name="field_size_plausible",
        passed=count >= MIN_CLASSIFIED_DRIVERS,
        detail="{} classified drivers (expected at least {})".format(
            count, MIN_CLASSIFIED_DRIVERS
        ),
    )


def _check_laps_present(payload: SessionPayload) -> IntegrityCheck:
    count = len(payload.laps)
    return IntegrityCheck(
        name="laps_present",
        passed=count > 0,
        detail="{} lap rows".format(count),
    )


def _check_every_driver_has_laps(payload: SessionPayload) -> IntegrityCheck:
    """A driver who started has laps. Missing laps means partial lap data.

    Drivers who never started (withdrawn, crashed on the formation lap) legitimately
    have none, so this reports the offenders rather than demanding a clean sweep,
    and tolerates a small number.
    """
    if not payload.laps:
        return IntegrityCheck(
            name="drivers_have_laps",
            passed=False,
            detail="no lap data to attribute",
        )

    drivers_with_laps = {row.driver for row in payload.laps}
    missing = sorted(
        row.driver for row in payload.results if row.driver not in drivers_with_laps
    )
    # Tolerate a couple of non-starters; a wholesale mismatch is the real signal.
    tolerated = 2
    return IntegrityCheck(
        name="drivers_have_laps",
        passed=len(missing) <= tolerated,
        detail=(
            "all classified drivers have laps"
            if not missing
            else "{} classified driver(s) without laps: {}".format(
                len(missing), ", ".join(missing[:5])
            )
        ),
    )


def _check_lap_numbering(payload: SessionPayload) -> IntegrityCheck:
    """Lap numbers per driver must be unique and gapless.

    A duplicate means the same lap was ingested twice; a hole means a lap was
    dropped in transit. Both are invisible in aggregate statistics — an average
    lap time computed over 90% of the laps looks entirely reasonable.
    """
    laps_by_driver: Dict[str, List[int]] = {}
    for row in payload.laps:
        laps_by_driver.setdefault(row.driver, []).append(row.lap)

    offenders: List[str] = []
    for driver, laps in laps_by_driver.items():
        unique = set(laps)
        if len(unique) != len(laps):
            offenders.append("{} (duplicate laps)".format(driver))
            continue
        # Laps run from the driver's first recorded lap to their last with no holes.
        expected_span = max(unique) - min(unique) + 1
        if expected_span != len(unique):
            offenders.append("{} (missing laps)".format(driver))

    return IntegrityCheck(
        name="lap_numbering_contiguous",
        passed=not offenders,
        detail=(
            "{} drivers with contiguous lap numbering".format(len(laps_by_driver))
            if not offenders
            else "; ".join(offenders[:5])
        ),
    )


def _check_stint_laps_reconcile(payload: SessionPayload) -> IntegrityCheck:
    """Stint lap totals must equal the driver's lap count.

    Stints are derived from the same laps, so a mismatch means the derivation
    dropped rows rather than that the race was unusual.
    """
    if not payload.stints:
        return IntegrityCheck(
            name="stint_laps_reconcile",
            passed=not payload.laps,
            detail="no stints derived from {} lap rows".format(len(payload.laps)),
        )

    lap_counts: Dict[str, int] = {}
    for row in payload.laps:
        lap_counts[row.driver] = lap_counts.get(row.driver, 0) + 1

    stint_totals: Dict[str, int] = {}
    for row in payload.stints:
        stint_totals[row.driver] = stint_totals.get(row.driver, 0) + row.laps

    offenders = [
        "{} ({} laps vs {} in stints)".format(driver, count, stint_totals.get(driver, 0))
        for driver, count in lap_counts.items()
        if stint_totals.get(driver, 0) != count
    ]

    return IntegrityCheck(
        name="stint_laps_reconcile",
        passed=not offenders,
        detail=(
            "stint totals match lap counts for {} drivers".format(len(lap_counts))
            if not offenders
            else "; ".join(offenders[:5])
        ),
    )


def _check_pit_stops_within_stints(payload: SessionPayload) -> IntegrityCheck:
    """A driver cannot make more stops than they have stints.

    Each completed stop opens a new stint, so stops should be at most
    ``stints - 1``. More stops than that means stops were double-counted.
    """
    stint_counts: Dict[str, int] = {}
    for row in payload.stints:
        stint_counts[row.driver] = stint_counts.get(row.driver, 0) + 1

    stop_counts: Dict[str, int] = {}
    for row in payload.pit_stops:
        stop_counts[row.driver] = stop_counts.get(row.driver, 0) + 1

    offenders = [
        "{} ({} stops, {} stints)".format(driver, stops, stint_counts.get(driver, 0))
        for driver, stops in stop_counts.items()
        if stops > max(stint_counts.get(driver, 0) - 1, 0)
    ]

    return IntegrityCheck(
        name="pit_stops_within_stints",
        passed=not offenders,
        detail=(
            "{} drivers with consistent stop/stint counts".format(len(stop_counts))
            if not offenders
            else "; ".join(offenders[:5])
        ),
    )


# ── Gap accounting ───────────────────────────────────────────────────────────

#: States that mean the historical record is not whole. ``UNAVAILABLE`` is
#: excluded on purpose — a race that has not run yet is not missing data.
GAP_STATES = (SessionState.PENDING, SessionState.PARTIAL, SessionState.FAILED)


def summarise(
    from_season: int,
    to_season: int,
    expected_keys: Iterable[str],
    states: Iterable[SessionIngestState],
    depth: IngestDepth = IngestDepth.FULL,
) -> CompletenessSummary:
    """Diff the expected-session manifest against recorded outcomes.

    An expected session with no state row at all counts as ``pending`` and an
    open gap — never having tried is indistinguishable, from the dataset's point
    of view, from having tried and failed.
    """
    # A session ingested only to results depth does not satisfy a full-depth
    # question. Treating it as complete would report lap data we never fetched
    # as present — the exact silent gap this module exists to prevent.
    by_key = {
        state.key: state
        for state in states
        if depth is IngestDepth.RESULTS or state.depth is IngestDepth.FULL
    }
    counts = {state: 0 for state in SessionState}
    open_gaps: List[str] = []

    for key in expected_keys:
        state = by_key.get(key)
        current = state.state if state else SessionState.PENDING
        counts[current] += 1
        if current in GAP_STATES:
            open_gaps.append(key)

    return CompletenessSummary(
        from_season=from_season,
        to_season=to_season,
        depth=depth,
        expected=sum(counts.values()),
        complete=counts[SessionState.COMPLETE],
        partial=counts[SessionState.PARTIAL],
        failed=counts[SessionState.FAILED],
        pending=counts[SessionState.PENDING],
        unavailable=counts[SessionState.UNAVAILABLE],
        open_gaps=sorted(open_gaps),
    )
