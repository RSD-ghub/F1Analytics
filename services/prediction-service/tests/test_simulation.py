"""Championship simulation tests.

The properties that matter: title probabilities must be a real distribution,
they must respond correctly to points gaps and races remaining, and constructor
numbers must come from summing *within* each simulated season rather than across
marginals — the last one is a genuinely easy mistake that produces plausible but
badly wrong team odds.
"""

import pytest

from app.models.schemas import DriverFeatures
from app.services.model import RaceModel
from tests.conftest import make_model
from app.services.simulation import (
    current_points_from,
    points_for,
    simulate_championship,
)
from app.services.ingestion_client import RaceResult


def _driver(name, form, team="Team"):
    return DriverFeatures(
        driver=name,
        team=team,
        avg_finish_recent=form,
        avg_finish_season=form,
        team_avg_finish=form,
        circuit_avg_finish=form,
        races_completed=10,
    )


FIELD = [
    _driver("Leader", 2.0, team="Alpha"),
    _driver("Chaser", 3.0, team="Alpha"),
    _driver("Third", 6.0, team="Beta"),
    _driver("Fourth", 10.0, team="Beta"),
]


def _simulate(points, rounds, runs=3000, seed=99):
    return simulate_championship(
        model=make_model(runs=runs),
        drivers=FIELD,
        current_points=points,
        remaining_rounds=rounds,
        season=2026,
        as_of_round=len(rounds) and 10 or 10,
        runs=runs,
        seed=seed,
    )


# ── Distribution properties ──────────────────────────────────────────────────


def test_title_probabilities_sum_to_one():
    forecast = _simulate({"Leader": 200, "Chaser": 180}, [11, 12, 13])
    assert sum(d.p_champion for d in forecast.drivers) == pytest.approx(1.0, abs=1e-9)


def test_constructor_probabilities_sum_to_one():
    forecast = _simulate({"Leader": 200, "Chaser": 180}, [11, 12, 13])
    assert sum(c.p_champion for c in forecast.constructors) == pytest.approx(
        1.0, abs=1e-9
    )


def test_every_driver_appears_exactly_once():
    forecast = _simulate({}, [11, 12])
    assert sorted(d.driver for d in forecast.drivers) == [
        "Chaser", "Fourth", "Leader", "Third",
    ]


def test_results_are_ordered_by_title_probability():
    forecast = _simulate({"Leader": 300}, [11, 12, 13])
    probabilities = [d.p_champion for d in forecast.drivers]
    assert probabilities == sorted(probabilities, reverse=True)


# ── Directionality ───────────────────────────────────────────────────────────


def test_an_unassailable_lead_is_a_near_certainty():
    """Three races left, 200 points clear: at most 75 points are available."""
    forecast = _simulate({"Leader": 400, "Chaser": 200}, [11, 12, 13])
    leader = next(d for d in forecast.drivers if d.driver == "Leader")

    assert leader.p_champion == 1.0


def test_a_points_lead_increases_title_probability():
    level = _simulate({}, [11, 12, 13])
    ahead = _simulate({"Chaser": 100}, [11, 12, 13])

    chaser_level = next(d for d in level.drivers if d.driver == "Chaser").p_champion
    chaser_ahead = next(d for d in ahead.drivers if d.driver == "Chaser").p_champion
    assert chaser_ahead > chaser_level


def test_more_remaining_races_erode_a_fixed_lead():
    """Time is what lets a faster rival overturn a deficit."""
    points = {"Chaser": 60}  # slower driver, but currently ahead
    one_race = _simulate(points, [11])
    many_races = _simulate(points, list(range(11, 23)))

    chaser_short = next(d for d in one_race.drivers if d.driver == "Chaser").p_champion
    chaser_long = next(d for d in many_races.drivers if d.driver == "Chaser").p_champion
    assert chaser_short > chaser_long


def test_no_remaining_races_locks_in_the_current_leader():
    forecast = _simulate({"Third": 500}, [])
    third = next(d for d in forecast.drivers if d.driver == "Third")

    assert third.p_champion == 1.0
    assert third.expected_final_points == 500.0


# ── Constructors ─────────────────────────────────────────────────────────────


def test_constructor_points_aggregate_both_cars():
    forecast = _simulate({"Leader": 200, "Chaser": 180}, [])
    alpha = next(c for c in forecast.constructors if c.team == "Alpha")

    assert alpha.current_points == 380.0


def test_constructor_odds_exceed_either_driver_alone():
    """The correlation check.

    Alpha wins the constructors' title whenever *either* car does well, so its
    probability must beat either driver's individual title odds. Summing marginals
    instead of aggregating within each simulated season breaks this.
    """
    forecast = _simulate({}, [11, 12, 13])
    alpha = next(c for c in forecast.constructors if c.team == "Alpha")
    leader = next(d for d in forecast.drivers if d.driver == "Leader")

    assert alpha.p_champion > leader.p_champion


# ── Reproducibility and provenance ───────────────────────────────────────────


def test_same_seed_reproduces_the_forecast():
    first = _simulate({"Leader": 100}, [11, 12], seed=7)
    second = _simulate({"Leader": 100}, [11, 12], seed=7)

    assert [d.model_dump() for d in first.drivers] == [
        d.model_dump() for d in second.drivers
    ]


def test_forecast_records_its_provenance():
    forecast = _simulate({}, [11, 12, 13], runs=1000, seed=5)

    assert forecast.runs == 1000
    assert forecast.seed == 5
    assert forecast.remaining_rounds == [11, 12, 13]
    assert forecast.model_version == make_model().version
    assert forecast.generated_at is not None


def test_empty_field_is_not_an_error():
    forecast = simulate_championship(
        model=make_model(runs=100),
        drivers=[],
        current_points={},
        remaining_rounds=[11],
        season=2026,
        as_of_round=10,
        runs=100,
    )
    assert forecast.drivers == []
    assert forecast.runs == 0


# ── Points ───────────────────────────────────────────────────────────────────


def test_points_scale_matches_the_current_system():
    assert points_for(1) == 25.0
    assert points_for(10) == 1.0
    assert points_for(11) == 0.0
    assert points_for(0) == 0.0


def test_current_points_sum_per_driver():
    results = [
        RaceResult(season=2026, round=1, driver="A", position=1, points=25.0),
        RaceResult(season=2026, round=2, driver="A", position=2, points=18.0),
        RaceResult(season=2025, round=1, driver="A", position=1, points=25.0),
        RaceResult(season=2026, round=1, driver="B", position=2, points=18.0),
    ]
    table = current_points_from(results, 2026)

    assert table == {"A": 43.0, "B": 18.0}  # 2025 excluded
