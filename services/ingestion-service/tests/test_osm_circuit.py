"""Stitching a circuit out of OpenStreetMap's roads.

OSM does not store a circuit, it stores roads. Sepang arrives as thirty
separate raceway ways — the grand prix layout, two sub-circuits, a handling
course, the pit lane and access roads — with no relation ordering them and no
tag saying which belong to the lap.

So the layout is verified rather than guessed: the only loop accepted is one
whose measured length matches the official figure. These tests are about that
check holding, because the failure they prevent is a confident, wrong map.
"""

import pytest

from app.services.osm_circuit import (
    LENGTH_TOLERANCE,
    OSMUnavailable,
    path_length,
    stitch_lap,
    usable_ways,
)


def _way(way_id, nodes, points, name=None):
    return {
        "id": way_id,
        "nodes": list(nodes),
        "geometry": [{"lat": lat, "lon": lon} for lat, lon in points],
        "tags": {"name": name} if name else {},
    }


#: A square kilometre-ish loop, as four ways meeting at four nodes. Degrees are
#: converted at roughly 111km each, so this measures about 4.4km round.
SQUARE = [
    _way(1, [10, 20], [(0.00, 0.00), (0.01, 0.00)]),
    _way(2, [20, 30], [(0.01, 0.00), (0.01, 0.01)]),
    _way(3, [30, 40], [(0.01, 0.01), (0.00, 0.01)]),
    _way(4, [40, 10], [(0.00, 0.01), (0.00, 0.00)]),
]
SQUARE_METRES = 4448.0


def test_the_ways_are_stitched_into_one_lap():
    points = stitch_lap(SQUARE, SQUARE_METRES)

    assert len(points) >= 5
    assert path_length(points) == pytest.approx(SQUARE_METRES, rel=0.02)


def test_a_way_is_reversed_when_that_is_how_it_joins():
    """Ways are drawn in whatever direction the mapper happened to trace them.
    A lap that only follows them forwards finds nothing on a real circuit."""
    backwards = list(SQUARE)
    backwards[2] = _way(3, [40, 30], [(0.00, 0.01), (0.01, 0.01)])

    points = stitch_lap(backwards, SQUARE_METRES)

    assert path_length(points) == pytest.approx(SQUARE_METRES, rel=0.02)


def test_a_loop_of_the_wrong_length_is_refused():
    """The check has to be able to fail, or it is decoration. A wrong set of
    roads is out by a factor, not a percent — half a circuit drawn confidently
    is worse than an honest blank."""
    with pytest.raises(OSMUnavailable):
        stitch_lap(SQUARE, SQUARE_METRES * 3)


def test_no_official_length_means_no_map():
    """The length is the only thing separating the grand prix lap from the
    other roads on the site."""
    with pytest.raises(OSMUnavailable):
        stitch_lap(SQUARE, 0)


def test_an_open_run_of_road_is_not_a_lap():
    with pytest.raises(OSMUnavailable):
        stitch_lap(SQUARE[:2], SQUARE_METRES)


def test_a_shortcut_is_not_taken_when_the_full_lap_fits_the_length():
    """Sub-circuits join the layout at both ends, so a search that can walk
    down one will happily return a lap that uses it as a shortcut. The length
    check is what rejects it."""
    shortcut = SQUARE + [_way(9, [20, 40], [(0.01, 0.00), (0.00, 0.01)])]

    points = stitch_lap(shortcut, SQUARE_METRES)

    assert path_length(points) == pytest.approx(SQUARE_METRES, rel=LENGTH_TOLERANCE)


# ── What is not even a candidate ─────────────────────────────────────────────


def test_the_pit_lane_is_not_part_of_the_lap():
    ways = usable_ways(SQUARE + [_way(5, [10, 20], [(0, 0), (0.01, 0)], name="Pit Lane")])

    assert 5 not in [w["id"] for w in ways]


def test_sub_circuits_are_not_part_of_the_lap():
    extra = [
        _way(6, [50, 60], [(0, 0), (0.01, 0)], name="North Circuit"),
        _way(7, [60, 70], [(0, 0), (0.01, 0)], name="Handling Circuit"),
    ]
    ways = usable_ways(SQUARE + extra)

    assert [w["id"] for w in ways] == [1, 2, 3, 4]


def test_a_named_corner_is_kept():
    """Sepang's ways are named after its corners — Genting Curve, Langkawi
    Curve. Excluding by name must not take the circuit with it."""
    ways = usable_ways([_way(8, [1, 2], [(0, 0), (0.01, 0)], name="Genting Curve")])

    assert len(ways) == 1
