"""The strategy engine: the guide's method for a race plan, as arithmetic.

What matters is that the plan responds to the race the way the guide says a
pit wall should: a stop fewer where passing is hard, a round-number target, a
safety-car window that opens before the stop, the start tyre chosen by grid
slot, and a rejoin worked out against the actual cars around.
"""

from app.routers import bernie as bernie_router
from app.routers import strategy as strategy_router
from app.services.bernie import GUIDE_KEY, SYSTEM_PROMPT, guide_facts
from app.services.strategy import build_inputs, simulate
from app.services.strategy_engine import (
    Car,
    ENGINE_KEY,
    build_field,
    engine_facts,
    excerpt,
    find_driver,
    mentioned_driver,
    plan_race,
    target_lap,
)

PROFILE = {
    "races_in_sample": 4, "seasons": [2022, 2023, 2024, 2025],
    "race_laps": 57, "pit_loss_seconds": 22.5, "pit_loss_sample": 120,
    "compounds": {
        "SOFT": {"degradation_seconds_per_lap": 0.12, "degradation_sample": 20,
                 "pace_offset_seconds": -0.6, "longest_stint_laps": 20},
        "MEDIUM": {"degradation_seconds_per_lap": 0.07, "degradation_sample": 60,
                   "longest_stint_laps": 32},
        "HARD": {"degradation_seconds_per_lap": 0.045, "degradation_sample": 70,
                 "pace_offset_seconds": 0.45, "longest_stint_laps": 40},
    },
    "race_control_races": 4, "safety_car_races": 2, "virtual_safety_car_races": 1,
}
EASY = {"mean_places_changed": 3.9, "pole_win_rate": 0.3}
HARD = {"mean_places_changed": 1.6, "pole_starts": 4, "pole_wins": 3}

NAMES = ["Driver {}".format(chr(65 + i)) for i in range(20)]


def _sim():
    return simulate(build_inputs(PROFILE))


def _field(pace_step=0.12):
    qualifying = [{"driver": n, "team": "T{}".format(i // 2), "position": i + 1,
                   "grid_position": i + 1} for i, n in enumerate(NAMES)]
    practice = [{"driver": n, "long_run_seconds": 95 + pace_step * i, "long_run_laps": 10}
                for i, n in enumerate(NAMES)]
    return build_field(qualifying, practice)


# ── What kind of race ────────────────────────────────────────────────────────


def test_a_track_position_race_takes_a_stop_fewer():
    sim = _sim()
    easy = plan_race(sim, EASY)
    hard = plan_race(sim, HARD)
    assert easy["race_character"]["kind"] == "optimum strategy"
    assert hard["race_character"]["kind"] == "track position"
    assert hard["plans"][0]["stops"] < easy["plans"][0]["stops"]
    assert "a stop fewer" in hard["plans"][0]["why"]


def test_an_optimum_race_runs_the_quickest_plan():
    sim = _sim()
    plan = plan_race(sim, EASY)
    quickest = min(sim.plans, key=lambda p: sim.expected[p.name])
    assert plan["plans"][0]["stops"] == quickest.stops
    assert plan["plans"][0]["expected_delta_seconds"] == 0


def test_no_overtaking_history_is_planned_as_average_and_says_so():
    character = plan_race(_sim(), None)["race_character"]
    assert character["overtaking"] == "moderate"
    assert any("GENERIC" in r for r in character["reasons"])


# ── Plans ────────────────────────────────────────────────────────────────────


def test_the_target_lap_is_a_round_number_in_reach():
    """The guide's own example: optimum lap 18 becomes Plan A, lap 20."""
    assert target_lap(18, (13, 21)) == 20
    assert target_lap(23, (20, 26)) == 25
    # No round number within two laps: the optimum stands.
    assert target_lap(13, (11, 14)) == 13
    # Never outside the window.
    assert target_lap(18, (16, 19)) == 18


def test_plans_a_b_and_c_are_offered():
    plans = plan_race(_sim(), EASY)["plans"]
    labels = [p["label"] for p in plans]
    assert labels[:2] == ["A", "B"]
    assert plans[1]["stops"] != plans[0]["stops"]
    if "C" in labels:
        c = next(p for p in plans if p["label"] == "C")
        assert c["sequence"] == list(reversed(plans[0]["sequence"]))


def test_stints_cover_the_race():
    for plan in plan_race(_sim(), EASY)["plans"]:
        assert plan["stints"][0]["from_lap"] == 1
        assert plan["stints"][-1]["to_lap"] == 57
        assert sum(s["laps"] for s in plan["stints"]) == 57
        assert len(set(plan["sequence"])) >= 2


def test_the_safety_car_window_opens_before_the_stop():
    sc = plan_race(_sim(), EASY)["safety_car"]
    assert sc["windows"]
    for window in sc["windows"]:
        assert window["opens_lap"] < window["planned_lap"]
    assert any("not worth stopping" in line for line in sc["summary"])


# ── Start tyre ───────────────────────────────────────────────────────────────


def test_the_start_tyre_follows_the_grid_slot():
    sim, field = _sim(), _field()
    front = plan_race(sim, HARD, field, field[0])["start_tyre"]
    back = plan_race(sim, HARD, field, field[17])["start_tyre"]
    assert not front["hardest_first"]
    assert back["hardest_first"]
    assert "safety car" in back["why"]


def test_a_back_of_grid_car_starts_its_plan_on_the_harder_tyre():
    sim, field = _sim(), _field()
    plan = plan_race(sim, HARD, field, field[17])["plans"][0]
    hardness = {"SOFT": 0, "MEDIUM": 1, "HARD": 2}
    assert hardness[plan["sequence"][0]] >= hardness[plan["sequence"][-1]]


# ── Among the field ──────────────────────────────────────────────────────────


def test_the_rejoin_counts_the_cars_inside_the_pit_loss():
    sim, field = _sim(), _field(pace_step=0.0)
    traffic = plan_race(sim, EASY, field, field[4])["pit_stop"]
    # Equal pace and 0.6s a place: 22.5s of pit loss covers 37 places, so the
    # car drops to the back.
    assert traffic["running_position"] == 5
    assert traffic["places_lost"] == 15
    assert traffic["rejoin_position"] == 20


def test_a_slower_car_ahead_on_rejoin_can_be_cleared_by_staying_out():
    sim = _sim()
    me = Car("Me", "T", 1, 95.0)
    slow = Car("Slow", "T", 2, 95.5)
    traffic = plan_race(sim, EASY, [me, slow], me)["pit_stop"]
    assert traffic["rejoins_behind"] == "Slow"
    assert "clear_by_stopping_lap" in traffic or "past the window" in " ".join(traffic["summary"])


def test_the_undercut_is_on_against_a_close_car_ahead():
    sim = _sim()
    ahead = Car("Ahead", "T", 1, 95.0)
    me = Car("Me", "T", 2, 95.0)
    behind = Car("Behind", "T", 9, 95.0)
    undercut = plan_race(sim, EASY, [ahead, me, behind], me)["undercut"]
    assert undercut["ahead"]["driver"] == "Ahead"
    assert undercut["ahead"]["viable"] is (undercut["gain_seconds"] > 0.6)
    assert undercut["behind"]["threat"] is False


def test_no_car_named_means_no_field_sections():
    plan = plan_race(_sim(), EASY, _field(), None)
    assert "pit_stop" not in plan and "driver" not in plan


def test_cars_without_a_grid_slot_are_left_out():
    field = build_field([{"driver": "A", "position": 999}, {"driver": "B", "position": 3}])
    assert [c.driver for c in field] == ["B"]


def test_drivers_are_found_by_surname_and_in_questions():
    field = [Car("Lando Norris", "McLaren", 3), Car("Max Verstappen", "Red Bull", 1)]
    assert find_driver(field, "norris").grid == 3
    assert find_driver(field, "Max Verstappen").grid == 1
    assert find_driver(field, "nobody") is None
    assert mentioned_driver(field, "What should Norris do from P8?").driver == "Lando Norris"
    assert mentioned_driver(field, "who wins?") is None


# ── Rendering ────────────────────────────────────────────────────────────────


def test_engine_facts_name_the_car_and_every_plan():
    sim, field = _sim(), _field()
    facts = engine_facts(plan_race(sim, EASY, field, field[8]))
    key = next(k for k in facts if k.startswith(ENGINE_KEY + ": plan for"))
    assert "Driver I from P9" in key
    lines = " ".join(facts[key])
    assert "Plan A" in lines and "Plan B" in lines and "Plan S" in lines
    assert "target lap" in lines


def test_an_excerpt_starts_at_the_sentence_that_answers():
    text = ("The weather was fine. Track position is key where it is hard to "
            "overtake. You will do a sub-optimal race to keep it. Lunch was late.")
    out = excerpt(text, "track position hard to overtake", limit=120)
    assert out.startswith("Track position is key")
    assert "weather" not in out


# ── The route and Bernie ─────────────────────────────────────────────────────


class _Settings:
    ingestion_service_url = "http://ingestion"
    downstream_timeout_seconds = 1.0


def _ingestion(guide_hits):
    qualifying = [{"driver": n, "team": "T", "position": i + 1, "grid_position": i + 1}
                  for i, n in enumerate(["Lando Norris", "Max Verstappen", "Oscar Piastri"])]

    async def get(self, path, params=None):
        if path.endswith("/strategy"):
            return PROFILE
        if path.startswith("/circuits/"):
            return {"official": {"scheduled_laps": 57}, "stats": HARD}
        if path == "/data/qualifying":
            return qualifying
        if path == "/data/practice":
            return []
        if path == "/guide/search":
            if guide_hits is None:
                raise RuntimeError("ingestion unreachable")
            return guide_hits
        raise AssertionError(path)

    return get


GUIDE_HITS = [{
    "title": "A Strategy Book", "author": "A. Strategist", "chapter": 15,
    "chapter_title": "Optimum Planning", "score": 9.0,
    "text": "We end up with two kinds of race. Track position is key where it is "
            "difficult to overtake.",
}]


async def test_the_route_returns_a_plan_with_citations(monkeypatch):
    monkeypatch.setattr(strategy_router.ServiceClient, "get", _ingestion(GUIDE_HITS))
    body = await strategy_router.race_strategy(2026, 7, driver="Piastri", settings=_Settings())
    assert body["plan"]["driver"]["name"] == "Oscar Piastri"
    assert body["plan"]["race_character"]["kind"] == "track position"
    cite = body["guide"]["race_character"][0]
    assert cite["chapter"] == 15 and cite["title"] == "A Strategy Book"
    assert "text" not in cite and cite["excerpt"]
    assert body["guide_note"] is None
    assert [c["driver"] for c in body["field"]][:1] == ["Lando Norris"]


async def test_the_route_still_plans_when_the_guide_is_down(monkeypatch):
    monkeypatch.setattr(strategy_router.ServiceClient, "get", _ingestion(None))
    body = await strategy_router.race_strategy(2026, 7, driver=None, settings=_Settings())
    assert body["plan"]["plans"]
    assert body["guide"] == {}
    assert "could not be searched" in body["guide_note"]


async def test_an_unknown_driver_is_reported_not_guessed(monkeypatch):
    monkeypatch.setattr(strategy_router.ServiceClient, "get", _ingestion([]))
    body = await strategy_router.race_strategy(2026, 7, driver="Senna", settings=_Settings())
    assert body["driver_not_found"] is True
    assert "holds no book" in body["guide_note"]


async def test_bernie_plans_for_the_car_the_question_names(monkeypatch):
    monkeypatch.setattr(bernie_router.ServiceClient, "get", _ingestion(GUIDE_HITS))
    facts = await bernie_router._strategy_facts(
        _Settings(), 2026, 7, "what should Verstappen do from second?")
    assert any("Max Verstappen from P2" in key for key in facts)


async def test_bernie_gets_guide_passages_labelled_with_their_chapter(monkeypatch):
    monkeypatch.setattr(bernie_router.ServiceClient, "get", _ingestion(GUIDE_HITS))
    facts = await bernie_router._guide_facts(_Settings(), "is track position key here?")
    assert facts[GUIDE_KEY][0].startswith("[A Strategy Book, chapter 15: Optimum Planning]")


async def test_bernie_is_told_when_the_guide_is_unreachable(monkeypatch):
    monkeypatch.setattr(bernie_router.ServiceClient, "get", _ingestion(None))
    facts = await bernie_router._guide_facts(_Settings(), "is track position key here?")
    assert "could not be searched" in facts["note on the strategy guide"]


def test_weak_guide_passages_are_dropped():
    hits = [dict(GUIDE_HITS[0], score=10.0), dict(GUIDE_HITS[0], score=2.0)]
    assert len(guide_facts(hits)[GUIDE_KEY]) == 1
    assert guide_facts([]) == {}


def test_the_prompt_keeps_the_authors_experience_hers():
    assert "not yours" in SYSTEM_PROMPT
    assert "strategy engine" in SYSTEM_PROMPT
