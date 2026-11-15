"""Driver pages and the pit stop review: retirements are not finishes."""

from app.services.driver_pages import (
    classified, driver_profile, pit_stop_review, season_drivers,
)


def _row(driver, rnd, pos, grid, cls=None, pts=0.0, status="Finished", season=2026, team="T"):
    return {
        "driver": driver, "season": season, "round": rnd, "race_name": f"R{rnd}",
        "team": team, "position": pos, "grid_position": grid, "points": pts,
        "classified_position": str(pos) if cls is None else cls, "status": status,
    }


def test_a_retirement_is_not_a_finishing_position():
    assert classified(_row("A", 1, 3, 5, cls="R", status="Engine")) is None
    assert classified(_row("A", 1, 3, 5)) == 3
    # Old rows without the raw classification fall back to the status.
    assert classified({"position": 2, "status": "Finished", "classified_position": ""}) == 2
    assert classified({"position": 2, "status": "Accident", "classified_position": ""}) is None
    assert classified({"position": 7, "status": "+1 Lap", "classified_position": ""}) == 7


def test_season_drivers_rank_by_points_and_count_only_real_podiums():
    rows = [
        _row("A", 1, 1, 2, pts=25), _row("A", 2, 2, 1, cls="R", status="Engine"),
        _row("B", 1, 2, 1, pts=18), _row("B", 2, 1, 3, pts=25),
    ]
    table = season_drivers(rows)
    assert [d["driver"] for d in table] == ["B", "A"]
    a = table[1]
    assert (a["wins"], a["podiums"], a["retirements"], a["starts"]) == (1, 1, 1, 2)


def test_driver_profile_splits_season_and_career():
    rows = [
        _row("A", 1, 4, 8, pts=12), _row("A", 2, 15, 0, cls="R", status="Gearbox"),
        _row("A", 5, 1, 1, pts=25, season=2025, team="Old"),
    ]
    profile = driver_profile("A", 2026, rows)
    assert [r["places_gained"] for r in profile["races"]] == [4, None]
    assert profile["summary"]["points"] == 12
    assert [c["season"] for c in profile["career"]] == [2026, 2025]
    assert profile["career_total"]["wins"] == 1
    assert driver_profile("Nobody", 2026, []) is None


def test_pit_stop_review_lays_out_stints_and_groups_by_stop_count():
    results = [_row("A", 1, 1, 3), _row("B", 1, 2, 1), _row("C", 1, 9, 2, cls="R", status="Brakes")]
    stints = [
        {"driver": "A", "stint": 1, "compound": "MEDIUM", "laps": 20},
        {"driver": "A", "stint": 2, "compound": "HARD", "laps": 30},
        {"driver": "B", "stint": 1, "compound": "SOFT", "laps": 15},
        {"driver": "B", "stint": 2, "compound": "MEDIUM", "laps": 15},
        {"driver": "B", "stint": 3, "compound": "MEDIUM", "laps": 20},
        {"driver": "C", "stint": 1, "compound": "SOFT", "laps": 3},
    ]
    stops = [
        {"driver": "A", "stop": 1, "lap": 20, "duration_seconds": 22.9},
        {"driver": "B", "stop": 1, "lap": 15, "duration_seconds": 21.4},
        {"driver": "B", "stop": 2, "lap": 30, "duration_seconds": 0.0},
    ]
    review = pit_stop_review(results, stints, stops)

    a = review["drivers"][0]
    assert a["driver"] == "A"
    assert [(s["from_lap"], s["to_lap"]) for s in a["stints"]] == [(1, 20), (21, 50)]
    assert review["laps"] == 50
    # An unrecorded duration is absent, not zero, and not ranked.
    assert review["drivers"][1]["stops"][1]["duration_seconds"] is None
    assert [s["driver"] for s in review["fastest_stops"]] == ["B", "A"]
    # The retired car does not count towards any strategy group.
    assert [(g["stops"], g["drivers"]) for g in review["strategies"]] == [(1, 1), (2, 1)]
    assert review["strategies"][0]["mean_places_gained"] == 2


def test_a_race_with_no_lap_data_says_so():
    review = pit_stop_review([_row("A", 1, 1, 1)], [], [])
    assert review["has_stint_data"] is False
    assert review["strategies"] == []
    assert pit_stop_review([], [], []) is None
