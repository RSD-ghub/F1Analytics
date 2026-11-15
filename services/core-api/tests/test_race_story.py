"""The Race page's data: a finished race lap by lap, beside our forecasts."""

from app.services.race_story import neutral_laps, race_story


def _result(driver, position, grid, team="T", status="Finished"):
    return {"season": 2026, "round": 21, "race_name": "Las Vegas Grand Prix",
            "driver": driver, "team": team, "position": position,
            "classified_position": str(position) if position else "R",
            "grid_position": grid, "status": status}


def _lap(driver, lap, position, seconds, compound="MEDIUM", stint=1):
    return {"driver": driver, "lap": lap, "position": position,
            "lap_time_seconds": seconds, "compound": compound, "stint": stint}


RESULTS = [_result("A", 1, 2), _result("B", 2, 1), _result("C", None, 3, status="Retired")]
LAPS = [
    _lap("B", 1, 1, 90.0), _lap("A", 1, 2, 90.5), _lap("C", 1, 3, 91.0),
    _lap("B", 2, 2, 110.0, "HARD", 2), _lap("A", 2, 1, 89.0), _lap("C", 2, 3, 0.0),
    _lap("B", 3, 2, 89.0, "HARD", 2), _lap("A", 3, 1, 89.0),
]


def story(**kw):
    args = dict(results=RESULTS, laps=LAPS, pit_stops=[{"driver": "B", "lap": 2}],
                messages=[], predictions=[])
    args.update(kw)
    return race_story(**args)


def test_places_gaps_tyres_and_stops_per_lap():
    s = story()
    assert s["total_laps"] == 3 and [d["driver"] for d in s["drivers"]] == ["A", "B", "C"]
    a, b, c = s["drivers"]
    assert a["positions"] == [2, 1, 1] and b["positions"] == [1, 2, 2]
    assert b["gaps"] == [0.0, 20.5, 20.5]
    assert a["gaps"] == [0.5, 0.0, 0.0]
    assert b["compounds"] == ["MEDIUM", "HARD", "HARD"] and b["tyre_ages"] == [1, 1, 2]
    assert b["stops"] == [2]


def test_a_lap_without_a_time_makes_the_gap_unknown_not_wrong():
    c = story()["drivers"][2]
    assert c["gaps"][0] == 1.0 and c["gaps"][1] is None
    assert c["positions"] == [3, 3, None] and c["last_lap"] == 2 and c["finish"] is None


def test_neutralised_laps():
    messages = [
        {"lap": 5, "category": "SafetyCar", "message": "SAFETY CAR DEPLOYED"},
        {"lap": 8, "category": "SafetyCar", "message": "SAFETY CAR IN THIS LAP"},
        {"lap": 20, "category": "SafetyCar", "message": "VIRTUAL SAFETY CAR DEPLOYED"},
        {"lap": 21, "category": "SafetyCar", "message": "VIRTUAL SAFETY CAR ENDING"},
        {"lap": 30, "category": "Flag", "message": "RED FLAG"},
        {"lap": 32, "category": "Other", "message": "ROLLING START"},
    ]
    laps = neutral_laps(messages, 50)
    assert [n for n, k in laps.items() if k == "SC"] == [5, 6, 7, 8, 31]
    assert [n for n, k in laps.items() if k == "VSC"] == [20, 21]
    assert laps[30] == "RED"


def test_forecasts_are_set_beside_the_result():
    predictions = [
        {"window": "pre_quali", "published_markets": ["podium", "points"],
         "driver_probabilities": [{"driver": "C", "p_podium": 0.9}, {"driver": "A", "p_podium": 0.8},
                                  {"driver": "B", "p_podium": 0.7}]},
        {"window": "final_grid", "published_markets": ["win", "podium", "points"],
         "driver_probabilities": [{"driver": "B", "p_win": 0.5, "p_podium": 0.9},
                                  {"driver": "A", "p_win": 0.3, "p_podium": 0.8}]},
    ]
    pre, final = story(predictions=predictions)["forecasts"]
    assert pre["window"] == "pre_quali" and pre["favourite"] is None and pre["winner_chance"] is None
    assert pre["podium_picks"] == ["C", "A", "B"] and pre["podium_hits"] == 2
    assert pre["drivers"]["A"]["win"] is None
    assert final["favourite"] == "B" and final["favourite_won"] is False and final["winner_chance"] == 0.3


def test_a_race_with_no_laps_still_has_its_result():
    s = story(laps=[])
    assert s["has_laps"] is False and s["total_laps"] == 0 and len(s["drivers"]) == 3
    assert race_story([], [], [], [], []) is None


class _Client:
    def __init__(self, name, routes, fail=False):
        self.routes, self.fail, self.calls = routes, fail, []

    async def get(self, path, params=None):
        self.calls.append((path, params))
        if self.fail:
            raise RuntimeError("down")
        for prefix, payload in self.routes.items():
            if path.startswith(prefix):
                return payload
        return None


async def _call(monkeypatch, prediction_fails=False, results=RESULTS):
    from app.config import Settings
    from app.routers import drivers

    ingestion = _Client("ingestion", {"/data/results": results, "/data/laps": LAPS,
                                      "/data/pit_stops": [], "/data/race_control": []})
    prediction = _Client("prediction", {"/predictions/": []}, fail=prediction_fails)
    monkeypatch.setattr(drivers, "_ingestion", lambda settings: ingestion)
    monkeypatch.setattr(drivers, "ServiceClient", lambda *a, **k: prediction)
    return await drivers.race_story_page(2026, 21, settings=Settings(jwt_secret="x" * 32)), ingestion


async def test_the_route_reads_a_whole_race_of_laps_and_survives_no_forecasts(monkeypatch):
    import pytest

    page, ingestion = await _call(monkeypatch, prediction_fails=True)
    assert page["total_laps"] == 3 and page["forecasts"] == []
    laps_call = next(p for path, p in ingestion.calls if path == "/data/laps")
    assert laps_call["limit"] >= 2000

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as caught:
        await _call(monkeypatch, results=[])
    assert caught.value.status_code == 404
