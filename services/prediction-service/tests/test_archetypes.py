"""Circuit archetype derivation — the metrics, not the clustering.

k-means on 40-odd points is library behaviour; what is worth guarding is what
goes *into* it. Every one of these metrics has already been wrong once: lap time
dominated the fit and was removed, straight fraction mislabels Barcelona as more
of a power circuit than Monza, and a circuit with no corner geometry was once
filed as the most technical track in the sport because zeroes sit at the extreme
of both axes.

Until now none of that was covered by a test, despite the model comments saying
otherwise.
"""

import importlib.util
import os

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "derive_archetypes",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "derive_archetypes.py"),
)
derive = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(derive)


class FakeDB:
    """The two collections the metric functions read."""

    def __init__(self, results, laps):
        self._c = {"results": _Coll(results), "laps": _Coll(laps)}

    def __getitem__(self, name):
        return self._c[name]


class _Coll:
    def __init__(self, rows):
        self._rows = rows

    def find(self, criteria=None, projection=None):
        rows = self._rows
        criteria = criteria or {}
        if "speed_trap_kph" in criteria:
            floor = criteria["speed_trap_kph"]["$gt"]
            rows = [r for r in rows if (r.get("speed_trap_kph") or 0) > floor]
        if "season" in criteria:
            cap = criteria["season"]["$lte"]
            rows = [r for r in rows if r["season"] <= cap]
        return iter(rows)


def _corpus(speeds, season=2024, circuit="Monza", laps_needed=1):
    results = [{"season": season, "round": 1, "circuit": circuit}]
    laps = [
        {"season": season, "round": 1, "speed_trap_kph": s}
        for s in speeds
    ]
    return FakeDB(results, laps)


# ── Speed trap ───────────────────────────────────────────────────────────────


def test_the_metric_map_is_keyed_canonically():
    """Keyed the same way the serving path looks up, so the artifact written
    here and the lookup there cannot disagree about a circuit's name."""
    db = _corpus([320.0, 322.0], circuit="Singapore")

    assert set(derive.top_speed_by_circuit(db, 2025, minimum_laps=1)) == {"marina bay"}


def test_the_trap_metric_is_the_median_not_the_maximum():
    """The fastest reading of a weekend is a tow or a DRS train. It describes
    one lap, not the circuit."""
    db = _corpus([320.0, 322.0, 324.0, 355.0])

    speeds = derive.top_speed_by_circuit(db, 2025, minimum_laps=1)

    assert speeds["monza"] == pytest.approx(323.0)


def test_laps_with_no_trap_reading_do_not_drag_the_median_down():
    """FastF1 leaves the trap empty on in-laps and out-laps. Averaging those in
    as zero would make every circuit look slow in proportion to its pit stops."""
    db = _corpus([0.0, 0.0, 330.0, 332.0])

    speeds = derive.top_speed_by_circuit(db, 2025, minimum_laps=1)

    assert speeds["monza"] == pytest.approx(331.0)


def test_sensor_artefacts_outside_a_plausible_range_are_discarded():
    """An F1 car does not trap at 30 km/h or at 900."""
    db = _corpus([30.0, 900.0, 328.0, 330.0])

    speeds = derive.top_speed_by_circuit(db, 2025, minimum_laps=1)

    assert speeds["monza"] == pytest.approx(329.0)


def test_a_circuit_with_too_thin_a_sample_is_left_out_entirely():
    """Better absent from the fit than present with a number built from nine
    laps — a missing circuit is excluded, a wrong one is clustered."""
    db = _corpus([330.0] * 9)

    assert derive.top_speed_by_circuit(db, 2025, minimum_laps=200) == {}


def test_seasons_after_the_cutoff_do_not_contribute():
    """The artifact is fitted on training seasons so held-out seasons stay held
    out. A metric that peeks is a leak into every prediction that uses it."""
    db = FakeDB(
        [{"season": 2021, "round": 1, "circuit": "Monza"},
         {"season": 2025, "round": 1, "circuit": "Monza"}],
        [{"season": 2021, "round": 1, "speed_trap_kph": 300.0},
         {"season": 2025, "round": 1, "speed_trap_kph": 360.0}],
    )

    speeds = derive.top_speed_by_circuit(db, 2021, minimum_laps=1)

    assert speeds["monza"] == pytest.approx(300.0)


# ── Straight fraction ────────────────────────────────────────────────────────


def test_the_longest_straight_wraps_around_the_pit_straight():
    """At several circuits the longest flat-out run is between the last corner
    and the first. Measuring only consecutive pairs misses it."""
    import pandas as pd

    # Three corners bunched together, with the long gap closing the lap.
    corners = pd.DataFrame({"X": [0.0, 10.0, 20.0], "Y": [0.0, 0.0, 0.0]})

    assert derive._longest_straight(corners) == pytest.approx(0.5)


def test_a_circuit_without_enough_corner_geometry_scores_zero():
    """Zero here means "unknown", and the caller excludes it rather than
    clustering it — a track with no geometry once landed as the most technical
    in the sport because zeroes sit at the extreme of both axes."""
    import pandas as pd

    assert derive._longest_straight(None) == 0.0
    assert derive._longest_straight(pd.DataFrame({"X": [0.0], "Y": [0.0]})) == 0.0


# ── Circuit identity ─────────────────────────────────────────────────────────


def test_one_track_under_two_names_resolves_to_one_key():
    """The corpus spells four circuits two ways each. Left alone that split
    their laps — Marina Bay had 258 rows and Singapore 40, of the same track —
    and the halves clustered separately into different archetypes."""
    from app.services.circuits import normalise

    assert normalise("Singapore") == normalise("Marina Bay")
    assert normalise("Monaco") == normalise("Monte Carlo")
    assert normalise("Miami Gardens") == normalise("Miami")
    assert normalise("Yas Marina") == normalise("Yas Island")


def test_normalising_still_absorbs_spacing_and_case():
    from app.services.circuits import normalise

    assert normalise("  MONZA  ") == normalise("Monza")
    assert normalise("Monte  Carlo") == normalise("monte carlo")


def test_an_unaliased_circuit_is_left_alone():
    from app.services.circuits import normalise

    assert normalise("Spa-Francorchamps") == "spa-francorchamps"
    assert normalise(None) == ""


def test_the_lookup_and_the_artifact_agree_on_the_key():
    """The artifact is written with one normaliser and read with another only if
    someone reimplements it. A disagreement shows up as every race at a circuit
    scoring `unknown`, which is silent."""
    from app.services.circuits import CircuitArchetypes, normalise

    archetypes = CircuitArchetypes({normalise("Marina Bay"): "technical"})

    assert archetypes.archetype_of("Singapore") == "technical"
    assert archetypes.archetype_of("Marina Bay") == "technical"


# ── The features stay out ────────────────────────────────────────────────────


def test_the_archetype_features_are_not_in_the_model():
    """Measured twice, withheld twice.

    The first measurement blamed coarse archetypes — Monza came out "balanced",
    Marina Bay came out "power" — and named straight-line speed as the fix. That
    fix shipped: traps are captured and the archetypes are visibly right now. The
    features were re-measured on them and were fractionally worse again, by
    almost the same margin (pre_quali 6.543% -> 6.526%, post_quali 8.290% ->
    8.265%).

    This guards the conclusion, not the code. Re-admitting these names is a
    decision that needs a measurement behind it, and two now say otherwise.
    """
    from app.services.model import BASE_FEATURES, WITHHELD_FEATURES, feature_names

    for name in WITHHELD_FEATURES:
        assert name not in BASE_FEATURES, "{} was re-admitted without a measurement".format(name)
        assert name not in feature_names(with_grid=True)


def test_the_withheld_features_are_still_computed():
    """Withdrawn from the model, not deleted. The next measurement needs them to
    exist, and deleting them is how a recorded negative result turns into work
    somebody redoes from scratch."""
    from app.models.schemas import DriverFeatures

    for name in ("driver_archetype_delta", "team_archetype_delta",
                 "team_regulation_mastery"):
        assert name in DriverFeatures.model_fields


def test_the_artifact_in_the_tree_is_the_speed_fitted_one():
    """v1 clustered on geometry alone and put Monza in 'balanced'. A stored
    prediction cites this version string, so it has to change when the metric
    set does."""
    from app.services.circuits import CircuitArchetypes

    archetypes = CircuitArchetypes.load()

    assert archetypes.version.startswith("archetypes-v2")
    assert archetypes.archetype_of("Monza") == "power"
    assert archetypes.archetype_of("Monte Carlo") == "technical"
    # And the alias resolves to the same track, not to UNKNOWN.
    assert archetypes.archetype_of("Monaco") == archetypes.archetype_of("Monte Carlo")
