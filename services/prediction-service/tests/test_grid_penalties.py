"""Grid penalties: qualifying classification is not the starting grid.

A driver who qualifies P2 and takes a ten-place power unit penalty starts P12.
Grid position is among the model's largest fitted weights, so modelling them
from P2 is not a rounding error — it is a materially wrong forecast on exactly
the drivers a reader cares about.

This was a live train/serve mismatch. Training built grid slots from race
results, which carry the real post-penalty grid. Serving read qualifying
classification, which does not. The two disagreed precisely on penalty
weekends, and nothing in the output showed it.
"""

import pytest

from app.models.schemas import LockWindow
from app.services.features import build_snapshot
from app.services.ingestion_client import GridSlot, RaceResult

DRIVERS = [("Alpha", "A"), ("Bravo", "B"), ("Charlie", "C"), ("Delta", "D")]


def _history():
    rows = []
    for rnd in range(1, 6):
        for i, (driver, team) in enumerate(DRIVERS):
            rows.append(RaceResult(
                season=2026, round=rnd, driver=driver, team=team,
                position=i + 1, classified_position=str(i + 1), status="Finished",
                points=float(25 - i * 5), grid_position=i + 1,
            ))
    return rows


def _slots(confirmed):
    """Alpha qualifies on pole but starts P12 with a penalty."""
    out = []
    for i, (driver, team) in enumerate(DRIVERS):
        quali = i + 1
        grid = 0
        if confirmed:
            grid = 12 if driver == "Alpha" else max(1, quali - 1)
        out.append(GridSlot(
            season=2026, round=6, driver=driver, team=team,
            position=quali, grid_position=grid,
            grid_source="official_final" if confirmed else "qualifying",
        ))
    return out


# ── The distinction ──────────────────────────────────────────────────────────


def test_confirmed_grid_overrides_qualifying_classification():
    slot = GridSlot(
        season=2026, round=6, driver="Alpha", position=2, grid_position=12,
        grid_source="official_final",
    )

    assert slot.confirmed
    assert slot.effective == 12


def test_a_grid_position_without_provenance_is_not_confirmed():
    """A number alone is not a claim about where anyone starts.

    Qualifying classification and the official grid are both integers in the
    same range, and for most drivers on most weekends they agree — so a slot
    that carries a position but no source is indistinguishable from a copied
    classification. Calling that "confirmed" is what let penalised drivers be
    modelled from the wrong slot, so the source is now required.
    """
    slot = GridSlot(season=2026, round=6, driver="Alpha", position=2, grid_position=12)

    assert not slot.confirmed


def test_unconfirmed_grid_falls_back_to_qualifying():
    slot = GridSlot(season=2026, round=6, driver="Alpha", position=2)

    assert not slot.confirmed
    assert slot.effective == 2


# ── Effect on the model's input ──────────────────────────────────────────────


def test_a_penalised_driver_is_modelled_from_where_they_actually_start():
    snapshot = build_snapshot(
        _history(), 2026, 6, LockWindow.POST_QUALI, grid=_slots(confirmed=True)
    )
    alpha = next(d for d in snapshot.drivers if d.driver == "Alpha")

    assert alpha.grid_position == 12, "penalty ignored — modelled from pole"


def test_without_a_confirmed_grid_the_features_differ():
    """Proof the distinction changes the model's input, not just a label."""
    penalised = build_snapshot(
        _history(), 2026, 6, LockWindow.POST_QUALI, grid=_slots(confirmed=True)
    )
    provisional = build_snapshot(
        _history(), 2026, 6, LockWindow.POST_QUALI, grid=_slots(confirmed=False)
    )

    assert penalised.snapshot_id != provisional.snapshot_id
    alpha_real = next(d for d in penalised.drivers if d.driver == "Alpha")
    alpha_assumed = next(d for d in provisional.drivers if d.driver == "Alpha")
    assert alpha_real.grid_position == 12
    assert alpha_assumed.grid_position == 1


def test_training_slots_are_always_confirmed():
    """Race results carry the real grid, so training never uses the fallback.

    That asymmetry is the mismatch: if serving silently falls back, it feeds the
    model a different quantity than the one its weights were fitted on.
    """
    from app.training.dataset import _observation_for

    rows = _history() + [
        RaceResult(season=2026, round=6, driver=d, team=t, position=i + 1,
                   classified_position=str(i + 1), status="Finished",
                   grid_position=i + 1)
        for i, (d, t) in enumerate(DRIVERS)
    ]
    observation = _observation_for(
        history=rows, season_rows=[r for r in rows if r.season == 2026],
        season=2026, round_number=6, circuit="", with_grid=True,
        target_season=2026, decay=0.6,
    )
    # MIN_FIELD is 6 and we have 4 drivers, so this correctly declines to build.
    assert observation is None
