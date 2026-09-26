"""Circuit geometry and what our corpus says about a track.

The risk here is a map that looks plausible and is wrong. A track drawn at the
wrong scale, with corner labels detached from their corners, or a "finish rate"
that quietly counts retirements as finishes, all render beautifully — so these
tests check the arithmetic rather than that something appeared.
"""

import math

import pytest

from app.services.circuit_map import (
    CIRCUIT_ALIASES,
    CircuitMapUnavailable,
    OUTLINE_POINTS,
    VIEWBOX,
    build_map,
    slug,
)
from app.services.circuit_stats import summarise
from app.services.fastf1_source import LOCATION_CORRECTIONS, _location


# ── Fakes: enough shape for the geometry, no FastF1 ──────────────────────────


class _Frame(list):
    """A telemetry frame: indexable by column, iterable by row."""

    def __init__(self, rows, columns):
        super().__init__(rows)
        self._columns = columns

    def __getitem__(self, key):
        if isinstance(key, str):
            return [row[self._columns.index(key)] for row in list.__iter__(self)]
        return list.__getitem__(self, key)


def _circle(points=400, radius=1000.0, speed=200):
    """A circular 'lap' — the shape whose scaling is easiest to check by hand."""
    rows = []
    for i in range(points):
        angle = 2 * math.pi * i / points
        rows.append((
            radius * math.cos(angle),
            radius * math.sin(angle),
            speed,
            i * 10,
        ))
    return _Frame(rows, ["X", "Y", "Speed", "Distance"])


class _Corners:
    def __init__(self, rows):
        self._rows = rows

    def __len__(self):
        return len(self._rows)

    def itertuples(self):
        class Row:
            def __init__(self, x, y, number):
                self.X, self.Y = x, y
                self.Number, self.Letter = number, ""
                self.Angle, self.Distance = 45.0, number * 100

        return (Row(*r) for r in self._rows)


# ── The key ──────────────────────────────────────────────────────────────────


def test_accents_are_folded_not_dropped():
    """Stripping them turned Montréal into "montr al" — it worked, and read as
    corruption in the database."""
    assert slug("Montréal") == "montreal"
    assert slug("São Paulo") == "sao paulo"


def test_punctuation_and_case_do_not_make_two_circuits():
    assert slug("Monte-Carlo") == slug("monte carlo") == "monte carlo"


# ── Geometry ─────────────────────────────────────────────────────────────────


def test_the_outline_is_downsampled_to_a_drawable_size():
    built = build_map("Ring", 2025, "Q", _circle(800), _Corners([]), 0.0)

    assert len(built.outline) == OUTLINE_POINTS
    assert len(built.speeds) == len(built.outline)


def test_a_short_lap_is_kept_whole():
    """Downsampling must not invent points or drop a small circuit's shape."""
    built = build_map("Ring", 2025, "Q", _circle(120), _Corners([]), 0.0)

    assert len(built.outline) == 120


def test_everything_lands_inside_the_viewbox():
    built = build_map("Ring", 2025, "Q", _circle(), _Corners([]), 0.0)

    assert all(0 <= x <= VIEWBOX and 0 <= y <= VIEWBOX for x, y in built.outline)


def test_corners_are_scaled_with_the_track_not_separately():
    """A corner is a point on the circuit. Scaling the two independently is the
    bug that puts turn one in the infield, and it looks fine until you know the
    track."""
    # A corner sitting exactly on the circle must land on the drawn line.
    built = build_map(
        "Ring", 2025, "Q", _circle(), _Corners([(1000.0, 0.0, 1)]), 0.0
    )

    corner = built.corners[0]
    nearest = min(
        math.dist((corner["x"], corner["y"]), point) for point in built.outline
    )
    assert nearest < 10, "corner is {:.1f} units off the track".format(nearest)


def test_rotation_turns_the_track_and_its_corners_together():
    """FastF1 stores coordinates in the timing frame and publishes a rotation
    to reach the orientation every broadcast uses. Applying it to the line and
    not the corners would peel the labels off."""
    upright = build_map(
        "Ring", 2025, "Q", _circle(), _Corners([(1000.0, 0.0, 1)]), 0.0
    )
    turned = build_map(
        "Ring", 2025, "Q", _circle(), _Corners([(1000.0, 0.0, 1)]), 90.0
    )

    moved = math.dist(
        (upright.corners[0]["x"], upright.corners[0]["y"]),
        (turned.corners[0]["x"], turned.corners[0]["y"]),
    )
    assert moved > 100, "rotation did not move the corner"
    nearest = min(
        math.dist((turned.corners[0]["x"], turned.corners[0]["y"]), p)
        for p in turned.outline
    )
    assert nearest < 10, "the corner came off the track when it turned"


def test_a_lap_with_almost_no_samples_is_refused():
    """Better no map than a triangle presented as a circuit."""
    with pytest.raises(CircuitMapUnavailable):
        build_map("Ring", 2025, "Q", _circle(10), _Corners([]), 0.0)


def test_speeds_are_carried_through_with_the_points_they_belong_to():
    rows = [(float(i), 0.0, 100 + i, i * 10) for i in range(400)]
    frame = _Frame(rows, ["X", "Y", "Speed", "Distance"])

    built = build_map("Straight", 2025, "Q", frame, _Corners([]), 0.0)

    assert built.top_speed_kph == max(built.speeds)
    assert built.slowest_kph == min(s for s in built.speeds if s > 0)
    # Monotonic input, monotonic output: the two lists stayed in step.
    assert built.speeds == sorted(built.speeds)


# ── Upstream we do not believe ───────────────────────────────────────────────


def test_bahrain_is_not_in_malaysia():
    """The published 2026 schedule puts the Bahrain Grand Prix at Kuala Lumpur.
    Left alone, Bahrain is described, characterised and drawn as Sepang."""
    assert _location(2026, "Bahrain Grand Prix", "Kuala Lumpur") == "Sakhir"


def test_a_correction_does_not_outlive_its_season():
    """Keyed on the season that carried the error, so a future event that
    legitimately races in Malaysia is left alone."""
    assert _location(2030, "Malaysian Grand Prix", "Kuala Lumpur") == "Kuala Lumpur"
    assert all(isinstance(key, tuple) for key in LOCATION_CORRECTIONS)


def test_an_uncorrected_location_passes_straight_through():
    assert _location(2026, "Italian Grand Prix", "Monza") == "Monza"


# ── What the corpus says ─────────────────────────────────────────────────────


def _result(season, rnd, driver, grid, finish, classified="1"):
    return {
        "season": season, "round": rnd, "driver": driver, "race_name": "GP",
        "grid_position": grid, "position": finish,
        "classified_position": classified,
    }


def test_a_retirement_is_not_a_finish():
    """``position`` carries an ordinal even for a car that stopped on lap
    three, so testing it made every driver at every circuit a finisher and
    Baku — which retires about one car in five — reported exactly 1.000."""
    results = [
        _result(2025, 1, "Alpha", 1, 1, classified="1"),
        _result(2025, 1, "Bravo", 2, 2, classified="2"),
        _result(2025, 1, "Charlie", 3, 3, classified="R"),
        _result(2025, 1, "Delta", 4, 4, classified="R"),
    ]

    assert summarise("Ring", results, [])["finish_rate"] == 0.5


def test_a_retirement_does_not_count_as_overtaking():
    """A car that stops on lap three 'loses' fifteen places without passing
    anyone, which would turn an attrition circuit into a fictional
    overtaking one."""
    results = [
        _result(2025, 1, "Alpha", 1, 1, classified="1"),
        _result(2025, 1, "Bravo", 2, 2, classified="2"),
        _result(2025, 1, "Charlie", 3, 20, classified="R"),
    ]

    stats = summarise("Ring", results, [])
    assert stats["places_changed_sample"] == 2
    assert stats["mean_places_changed"] == 0.0


def test_pole_conversion_counts_races_that_had_a_pole_sitter():
    """Counting over every race instead would read a missing grid as pole
    losing."""
    results = [
        _result(2024, 1, "Alpha", 1, 1),
        _result(2024, 1, "Bravo", 2, 2),
        _result(2025, 2, "Alpha", 0, 1),   # no grid recorded for this race
        _result(2025, 2, "Bravo", 0, 2),
    ]

    stats = summarise("Ring", results, [])
    assert stats["pole_starts"] == 1
    assert stats["pole_wins"] == 1


def test_a_rate_is_withheld_when_there_are_too_few_visits():
    """Two visits cannot establish the character of a circuit."""
    results = [_result(2025, 1, "Alpha", 1, 1), _result(2024, 1, "Alpha", 1, 2)]

    assert "pole_win_rate" not in summarise("Ring", results, [])


def test_a_single_win_each_is_nobody_owning_the_place():
    results = [
        _result(2024, 1, "Alpha", 1, 1),
        _result(2025, 2, "Bravo", 1, 1),
    ]

    assert "most_wins" not in summarise("Ring", results, [])


def test_the_lap_record_is_the_fastest_lap_we_hold():
    laps = [
        {"lap_time_seconds": 105.5, "driver": "Alpha", "season": 2024},
        {"lap_time_seconds": 103.009, "driver": "Bravo", "season": 2019},
        {"lap_time_seconds": 104.0, "driver": "Charlie", "season": 2025},
    ]

    record = summarise("Ring", [], laps)["fastest_lap"]
    assert record["seconds"] == 103.009
    assert record["driver"] == "Bravo"


# ── One venue, several names ─────────────────────────────────────────────────


def test_a_renamed_venue_is_one_circuit():
    """Upstream filed Abu Dhabi under "Abu Dhabi" to 2017, "Yas Marina" for
    2018 and "Yas Island" since. Three names, one track — and unresolved, the
    2026 race found a single prior visit, drew its layout from 2018 and
    reported a circuit record built from one race."""
    assert slug("Abu Dhabi") == slug("Yas Marina") == slug("Yas Island")


def test_accent_and_punctuation_variants_need_no_alias():
    """They collapse in the slug itself, so the hand-maintained map stays
    small enough to be read."""
    assert slug("Spa") == slug("Spa-Francorchamps")
    assert slug("Montreal") == slug("Montréal")
    assert "montreal" not in CIRCUIT_ALIASES
    assert "montréal" not in CIRCUIT_ALIASES


def test_two_circuits_sharing_a_race_name_stay_apart():
    """The Spanish Grand Prix is Barcelona through 2025 and Madrid in 2026;
    the European Grand Prix has been both Valencia and Baku. Keying on the
    race name would have fused them."""
    assert slug("Barcelona") != slug("Madrid")
    assert slug("Valencia") != slug("Baku")


def test_every_alias_points_at_a_settled_name():
    """An alias whose target is itself an alias resolves differently depending
    on which spelling arrives first."""
    for target in CIRCUIT_ALIASES.values():
        assert target not in CIRCUIT_ALIASES, (
            "{} is both an alias and a target".format(target)
        )
