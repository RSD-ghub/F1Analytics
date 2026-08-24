"""THE critical test. Runs in CI.

The product's entire claim is that its published accuracy is real. That claim
dies silently if a forecast for round N was built using data from round N — the
model looks excellent in backtests, the calibration curve looks healthy, and
every number is a lie. Nothing in the output reveals it.

The invariant asserted here is stronger than a spot check on individual features:

    Building features for round N from the full dataset must produce a
    byte-identical result to building them from a dataset where every row at or
    after round N has been physically deleted.

If any feature reaches forward, the two runs differ and this fails. New features
are covered automatically — that is the point of testing the invariant rather
than enumerating the features.
"""

import pytest

from app.models.schemas import LockWindow
from app.services.features import build_snapshot, snapshot_hash
from app.services.ingestion_client import GridSlot, RaceResult

DRIVERS = [
    ("Max Verstappen", "Red Bull"),
    ("Lando Norris", "McLaren"),
    ("Charles Leclerc", "Ferrari"),
    ("George Russell", "Mercedes"),
]

CIRCUITS = {1: "Sakhir", 2: "Jeddah", 3: "Melbourne", 4: "Suzuka", 5: "Sakhir"}


def _season(season: int, rounds: int):
    """A synthetic season where finishing order rotates each round."""
    rows = []
    for round_number in range(1, rounds + 1):
        for offset, (driver, team) in enumerate(DRIVERS):
            position = ((round_number + offset) % len(DRIVERS)) + 1
            rows.append(
                RaceResult(
                    season=season,
                    round=round_number,
                    race_name="Round {}".format(round_number),
                    circuit=CIRCUITS.get(round_number, "Unknown"),
                    driver=driver,
                    team=team,
                    position=position,
                    points=float(26 - position * 5),
                    grid_position=position,
                )
            )
    return rows


FULL = _season(2024, 8) + _season(2023, 8)


def _without_from(rows, season: int, from_round: int):
    """Physically remove everything at or after a round — the ground truth."""
    return [
        row
        for row in rows
        if not (row.season == season and row.round >= from_round)
    ]


# ── The invariant ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("target_round", [1, 2, 4, 5, 8])
def test_features_ignore_everything_at_or_after_the_target_round(target_round):
    """Full data and truncated data must produce identical features."""
    from_full = build_snapshot(
        FULL, season=2024, target_round=target_round,
        window=LockWindow.PRE_QUALI, circuit=CIRCUITS.get(target_round, ""),
    )
    from_truncated = build_snapshot(
        _without_from(FULL, 2024, target_round),
        season=2024, target_round=target_round,
        window=LockWindow.PRE_QUALI, circuit=CIRCUITS.get(target_round, ""),
    )

    assert from_full.snapshot_id == from_truncated.snapshot_id
    assert from_full.drivers == from_truncated.drivers


@pytest.mark.parametrize("target_round", [1, 2, 4, 8])
def test_invariant_holds_for_the_post_quali_window_too(target_round):
    """The grid is legitimate; the rest of the round still must not leak."""
    grid = [
        GridSlot(season=2024, round=target_round, driver=driver, team=team, position=i + 1)
        for i, (driver, team) in enumerate(DRIVERS)
    ]

    from_full = build_snapshot(
        FULL, season=2024, target_round=target_round,
        window=LockWindow.POST_QUALI, circuit=CIRCUITS.get(target_round, ""), grid=grid,
    )
    from_truncated = build_snapshot(
        _without_from(FULL, 2024, target_round),
        season=2024, target_round=target_round,
        window=LockWindow.POST_QUALI, circuit=CIRCUITS.get(target_round, ""), grid=grid,
    )

    assert from_full.snapshot_id == from_truncated.snapshot_id


def test_the_invariant_test_can_actually_fail():
    """Guard against a vacuous test.

    If the truncation helper did nothing, or every snapshot hashed the same, the
    tests above would pass while proving nothing. Data from a *later* round must
    change the features — otherwise there is no signal to leak in the first place.
    """
    round_four = build_snapshot(
        FULL, season=2024, target_round=4, window=LockWindow.PRE_QUALI
    )
    round_five = build_snapshot(
        FULL, season=2024, target_round=5, window=LockWindow.PRE_QUALI
    )

    assert round_four.snapshot_id != round_five.snapshot_id
    assert round_four.included_rounds == [1, 2, 3]
    assert round_five.included_rounds == [1, 2, 3, 4]


# ── Provenance ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("target_round", [2, 3, 6, 8])
def test_as_of_round_is_always_before_the_target(target_round):
    """The audit handle. A snapshot claiming as_of >= round is proof of leakage."""
    snapshot = build_snapshot(
        FULL, season=2024, target_round=target_round, window=LockWindow.PRE_QUALI
    )
    assert snapshot.as_of_round < target_round
    assert all(r < target_round for r in snapshot.included_rounds)


def test_round_one_has_no_current_season_history():
    snapshot = build_snapshot(
        FULL, season=2024, target_round=1, window=LockWindow.PRE_QUALI
    )
    assert snapshot.as_of_round == 0
    assert snapshot.included_rounds == []


def test_a_gap_in_ingested_data_is_visible_not_silent():
    """Missing round 3 changes what these features mean; it must be recorded."""
    with_gap = [row for row in FULL if not (row.season == 2024 and row.round == 3)]
    snapshot = build_snapshot(
        with_gap, season=2024, target_round=5, window=LockWindow.PRE_QUALI
    )
    assert snapshot.included_rounds == [1, 2, 4]


# ── Window separation ────────────────────────────────────────────────────────


def test_pre_quali_forecast_never_sees_the_grid():
    """A pre-quali call that used the grid would be mislabelled in the record.

    It would also flatter the model precisely where it claims to be doing the
    hard version of the task, so the grid is dropped rather than trusted.
    """
    grid = [
        GridSlot(season=2024, round=5, driver=driver, team=team, position=i + 1)
        for i, (driver, team) in enumerate(DRIVERS)
    ]
    snapshot = build_snapshot(
        FULL, season=2024, target_round=5, window=LockWindow.PRE_QUALI, grid=grid
    )

    assert all(d.grid_position == 0 for d in snapshot.drivers)


def test_post_quali_forecast_uses_the_grid():
    grid = [
        GridSlot(season=2024, round=5, driver=driver, team=team, position=i + 1)
        for i, (driver, team) in enumerate(DRIVERS)
    ]
    snapshot = build_snapshot(
        FULL, season=2024, target_round=5, window=LockWindow.POST_QUALI, grid=grid
    )

    assert [d.grid_position for d in snapshot.drivers] != [0, 0, 0, 0]
    assert snapshot.snapshot_id != build_snapshot(
        FULL, season=2024, target_round=5, window=LockWindow.PRE_QUALI
    ).snapshot_id


# ── Reproducibility ──────────────────────────────────────────────────────────


def test_snapshot_hash_is_stable_across_rebuilds():
    """Two builds moments apart must hash identically despite differing clocks."""
    first = build_snapshot(FULL, 2024, 5, LockWindow.PRE_QUALI)
    second = build_snapshot(FULL, 2024, 5, LockWindow.PRE_QUALI)

    # The hash must cover feature values only — never created_at, or an
    # identical rebuild would appear to be a different snapshot.
    assert first.snapshot_id == second.snapshot_id
    assert snapshot_hash(first) == snapshot_hash(second)


def test_snapshot_hash_changes_when_features_change():
    baseline = build_snapshot(FULL, 2024, 5, LockWindow.PRE_QUALI)
    altered = build_snapshot(FULL[:-4], 2024, 5, LockWindow.PRE_QUALI)

    assert baseline.snapshot_id != altered.snapshot_id


def test_input_order_does_not_change_the_snapshot():
    """Mongo makes no ordering promise; the snapshot must not depend on one."""
    shuffled = list(reversed(FULL))
    assert (
        build_snapshot(FULL, 2024, 5, LockWindow.PRE_QUALI).snapshot_id
        == build_snapshot(shuffled, 2024, 5, LockWindow.PRE_QUALI).snapshot_id
    )
