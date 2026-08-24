"""Reproducibility: a stored prediction must be re-derivable exactly.

This is what makes the public track record auditable rather than merely
published. Given a prediction's stored feature snapshot, model version and seed,
re-running the model must reproduce its probabilities exactly — so anyone can
check that a past call was what we say it was, and we cannot quietly restate one.

The three inputs are tested independently, because losing any one of them breaks
the guarantee in a different way.
"""

import pytest

from app.models.schemas import LockWindow
from app.services.features import build_snapshot
from app.services.ingestion_client import RaceResult
from tests.conftest import make_model
from app.services.predictor import derive_seed

DRIVERS = [
    ("Max Verstappen", "Red Bull"),
    ("Lando Norris", "McLaren"),
    ("Charles Leclerc", "Ferrari"),
    ("George Russell", "Mercedes"),
    ("Oscar Piastri", "McLaren"),
]


def _rows(season=2026, rounds=8):
    out = []
    for round_number in range(1, rounds + 1):
        for offset, (driver, team) in enumerate(DRIVERS):
            position = ((round_number + offset) % len(DRIVERS)) + 1
            out.append(
                RaceResult(
                    season=season, round=round_number, circuit="C{}".format(round_number),
                    driver=driver, team=team, position=position,
                    points=float(26 - position * 5), grid_position=position,
                )
            )
    return out


RESULTS = _rows()


def _probabilities(model, snapshot, seed):
    return [p.model_dump() for p in model.predict(snapshot.drivers, seed=seed)]


# ── The guarantee ────────────────────────────────────────────────────────────


def test_a_prediction_replays_exactly_from_its_stored_inputs():
    """Snapshot + version + seed reproduces the numbers, bit for bit."""
    snapshot = build_snapshot(RESULTS, 2026, 5, LockWindow.PRE_QUALI, circuit="C5")
    seed = derive_seed(2026, 5, LockWindow.PRE_QUALI)
    model = make_model(runs=2000)

    original = _probabilities(model, snapshot, seed)

    # Later, from storage: a fresh model instance of the same version, the
    # stored snapshot, the stored seed.
    replayed = _probabilities(make_model(runs=2000), snapshot, seed)

    assert original == replayed


def test_replay_survives_rebuilding_the_snapshot_from_source_data():
    """The stronger form: even the snapshot need not be trusted, only the data."""
    seed = derive_seed(2026, 5, LockWindow.PRE_QUALI)
    model = make_model(runs=2000)

    first = build_snapshot(RESULTS, 2026, 5, LockWindow.PRE_QUALI, circuit="C5")
    rebuilt = build_snapshot(
        list(reversed(RESULTS)), 2026, 5, LockWindow.PRE_QUALI, circuit="C5"
    )

    assert first.snapshot_id == rebuilt.snapshot_id
    assert _probabilities(model, first, seed) == _probabilities(model, rebuilt, seed)


# ── Each input matters ───────────────────────────────────────────────────────


def test_losing_the_seed_breaks_reproduction():
    """Why the seed is stored on the prediction rather than left implicit."""
    snapshot = build_snapshot(RESULTS, 2026, 5, LockWindow.PRE_QUALI)
    model = make_model(runs=2000)

    assert _probabilities(model, snapshot, 111) != _probabilities(model, snapshot, 222)


def test_changing_model_parameters_changes_the_output():
    """Why MODEL_VERSION must be bumped whenever parameters change.

    Without that, old and new predictions get averaged into one track record as
    though they came from the same system.
    """
    snapshot = build_snapshot(RESULTS, 2026, 5, LockWindow.PRE_QUALI)
    seed = derive_seed(2026, 5, LockWindow.PRE_QUALI)

    baseline = _probabilities(make_model(runs=2000, noise_scale=1.0), snapshot, seed)
    altered = _probabilities(make_model(runs=2000, noise_scale=2.5), snapshot, seed)

    assert baseline != altered


def test_changing_the_snapshot_changes_the_output():
    seed = derive_seed(2026, 5, LockWindow.PRE_QUALI)
    model = make_model(runs=2000)

    round_five = build_snapshot(RESULTS, 2026, 5, LockWindow.PRE_QUALI)
    round_six = build_snapshot(RESULTS, 2026, 6, LockWindow.PRE_QUALI)

    assert _probabilities(model, round_five, seed) != _probabilities(model, round_six, seed)


# ── Seed derivation ──────────────────────────────────────────────────────────


def test_seed_is_stable_for_the_same_forecast():
    """Derived from identity, so it survives even if the stored value were lost."""
    assert derive_seed(2026, 5, LockWindow.PRE_QUALI) == derive_seed(
        2026, 5, LockWindow.PRE_QUALI
    )


def test_seed_is_stable_across_processes():
    """The test that matters, and that an in-process check cannot make.

    ``hash()`` on a string is randomised per interpreter (PYTHONHASHSEED), so a
    seed derived from it looks perfectly stable within one test run and changes
    on every restart — silently breaking reproduction of every stored
    prediction. Running under a different hash seed is what exposes that.
    """
    import subprocess
    import sys

    script = (
        "import sys; sys.path.insert(0, '.');"
        "from app.services.predictor import derive_seed;"
        "from app.models.schemas import LockWindow;"
        "print(derive_seed(2026, 5, LockWindow.PRE_QUALI))"
    )
    seeds = set()
    for hash_seed in ("0", "1", "12345"):
        output = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, check=True,
            env={"PYTHONHASHSEED": hash_seed, "PATH": "/usr/bin:/bin"},
        )
        seeds.add(output.stdout.strip())

    assert len(seeds) == 1, "seed changed across processes: {}".format(seeds)
    assert seeds == {str(derive_seed(2026, 5, LockWindow.PRE_QUALI))}


def test_seed_differs_across_races_and_windows():
    """Shared seeds would correlate unrelated forecasts' sampling noise."""
    seeds = {
        derive_seed(2026, 5, LockWindow.PRE_QUALI),
        derive_seed(2026, 5, LockWindow.POST_QUALI),
        derive_seed(2026, 6, LockWindow.PRE_QUALI),
        derive_seed(2025, 5, LockWindow.PRE_QUALI),
    }
    assert len(seeds) == 4


def test_seed_is_within_numpy_range():
    """numpy's default_rng rejects seeds outside the 32-bit range."""
    for round_number in range(1, 25):
        for window in LockWindow:
            seed = derive_seed(2026, round_number, window)
            assert 0 <= seed < 2**31
