"""Strategy simulation tests: Bernie plans from arithmetic, not from nothing.

The failure this replaces was a strategist who, asked for a strategy, replied
that she had not been given one. So the property that matters most is that a
plan always exists — with this circuit's numbers when we hold them, with
labelled generic ones when we do not — and that the plans it ranks respond to
the inputs the way a pit wall would expect.
"""

from app.routers import bernie as bernie_router
from app.services.bernie import SYSTEM_PROMPT
from app.services.strategy import (
    GREEN_KEY,
    INPUTS_KEY,
    MIN_STINT_LAPS,
    NEUTRALISED_KEY,
    _optimise,
    _sequences,
    build_inputs,
    simulate,
    strategy_facts,
)


def _profile(**overrides):
    profile = {
        "races_in_sample": 4,
        "seasons": [2022, 2023, 2024, 2025],
        "race_laps": 66,
        "pit_loss_seconds": 21.4,
        "pit_loss_sample": 120,
        "compounds": {
            "SOFT": {"degradation_seconds_per_lap": 0.12, "degradation_sample": 20,
                     "pace_offset_seconds": -0.55, "longest_stint_laps": 22},
            "MEDIUM": {"degradation_seconds_per_lap": 0.07, "degradation_sample": 60,
                       "longest_stint_laps": 35},
            "HARD": {"degradation_seconds_per_lap": 0.04, "degradation_sample": 70,
                     "pace_offset_seconds": 0.45, "longest_stint_laps": 45},
        },
        "race_control_races": 4,
        "safety_car_races": 1,
        "virtual_safety_car_races": 2,
    }
    profile.update(overrides)
    return profile


class _Settings:
    ingestion_service_url = "http://ingestion"
    downstream_timeout_seconds = 1.0


# ── A plan always exists ─────────────────────────────────────────────────────


def test_a_circuit_with_no_history_still_gets_a_plan():
    facts = strategy_facts(simulate(build_inputs(None)))
    assert facts[GREEN_KEY], "no history must not mean no strategy"
    # ...and every input says it is generic, so Bernie can say so.
    assert all("GENERIC" in line for line in facts[INPUTS_KEY][:6])


def test_measured_inputs_are_labelled_with_their_sample():
    sources = build_inputs(_profile()).sources
    assert any("measured over 120 stops" in line for line in sources)
    assert not any("GENERIC" in line for line in sources)


def test_the_official_race_distance_wins_over_the_measured_one():
    inputs = build_inputs(_profile(race_laps=60), scheduled_laps=66)
    assert inputs.race_laps == 66
    assert "official" in inputs.sources[0]


# ── The plans make sense ─────────────────────────────────────────────────────


def test_every_plan_uses_two_compounds():
    """A dry race must use two different compounds."""
    assert all(len(set(seq)) >= 2 for seq in _sequences(3))


def test_plans_cover_the_race_with_legal_stints():
    inputs = build_inputs(_profile())
    for seq in _sequences(3):
        plan = _optimise(seq, inputs)
        laps = [0] + plan.pit_laps + [inputs.race_laps]
        stints = [b - a for a, b in zip(laps, laps[1:])]
        assert all(s >= MIN_STINT_LAPS for s in stints)
        assert sum(stints) == inputs.race_laps
        for lap, (lo, hi) in zip(plan.pit_laps, plan.windows):
            assert lo <= lap <= hi


def test_identical_plans_are_not_listed_twice():
    """Order does not change cost in this model, so S-M and M-S are one plan."""
    names = [p.name for p in simulate(build_inputs(_profile())).plans]
    assert len(names) == len(set(names))
    assert "M-S" not in names


def test_an_expensive_pit_lane_favours_fewer_stops():
    cheap = simulate(build_inputs(_profile(pit_loss_seconds=12.0)))
    dear = simulate(build_inputs(_profile(pit_loss_seconds=35.0)))
    assert dear.plans[0].stops < cheap.plans[0].stops
    assert dear.plans[0].stops == 1


def test_a_tyre_that_barely_wears_makes_a_one_stop_quickest():
    flat = _profile(compounds={
        c: {"degradation_seconds_per_lap": 0.01, "longest_stint_laps": 60,
            "pace_offset_seconds": o}
        for c, o in (("SOFT", -0.3), ("MEDIUM", 0.0), ("HARD", 0.3))
    })
    assert simulate(build_inputs(flat)).plans[0].stops == 1


def test_the_simulation_is_reproducible():
    """Asked twice, Bernie must not be handed two different best plans."""
    a = strategy_facts(simulate(build_inputs(_profile())))
    b = strategy_facts(simulate(build_inputs(_profile())))
    assert a == b


def test_safety_cars_are_simulated_and_reported():
    facts = strategy_facts(simulate(build_inputs(_profile())))
    assert any("simulated races" in line for line in facts[NEUTRALISED_KEY])
    assert "strategy simulation: not modelled" in facts


# ── Bernie is told to use it ─────────────────────────────────────────────────


def test_the_prompt_forbids_refusing_a_strategy_question():
    assert "give one" in SYSTEM_PROMPT
    assert "Never answer that you do not have the facts" in SYSTEM_PROMPT
    assert "GENERIC" in SYSTEM_PROMPT
    # The plan is a simulation, not a claim about what teams will do.
    assert "never say a team will run a plan" in SYSTEM_PROMPT


async def test_the_facts_pack_has_a_plan_even_when_ingestion_is_down(monkeypatch):
    async def down(self, path, params=None):
        raise RuntimeError("ingestion unreachable")

    monkeypatch.setattr(bernie_router.ServiceClient, "get", down)
    facts = await bernie_router._strategy_facts(_Settings(), 2026, 5)
    assert facts[GREEN_KEY]
    assert "generic figures" in facts["note on the strategy simulation"]


async def test_track_position_reaches_the_facts(monkeypatch):
    async def get(self, path, params=None):
        if path.endswith("/strategy"):
            return _profile()
        return {
            "official": {"scheduled_laps": 78},
            "stats": {"mean_places_changed": 1.4, "places_changed_sample": 70,
                      "pole_starts": 4, "pole_wins": 3},
        }

    monkeypatch.setattr(bernie_router.ServiceClient, "get", get)
    facts = await bernie_router._strategy_facts(_Settings(), 2026, 8)
    assert "78 laps (official" in facts[INPUTS_KEY][0]
    assert any("1.4 places" in line for line in facts["track position at this circuit"])
    assert "note on the strategy simulation" not in facts
