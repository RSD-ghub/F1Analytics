"""Race-day weather: what is shown, when nothing is, and Bernie's caveat."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services import weather

START = datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc)
CIRCUIT = {"location": {"lat": 19.40, "lon": -99.09}}
URL = "https://weather.test/v1/forecast"


def _payload(chances, rain=None):
    first = START - timedelta(hours=3)
    times = [(first + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(len(chances))]
    return {"hourly": {
        "time": times,
        "precipitation_probability": chances,
        "precipitation": rain or [0.0] * len(chances),
        "temperature_2m": [20.0 + i for i in range(len(chances))],
    }}


def _client(payload=None, status=200, seen=None):
    def handle(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=payload or {})
    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


@pytest.fixture(autouse=True)
def _empty_cache():
    weather._cache.clear()
    yield
    weather._cache.clear()


async def test_it_keeps_only_the_race_window():
    # 3h before start .. 5h after: the window is start-1h .. start+3h.
    payload = _payload([90, 90, 10, 20, 30, 40, 50, 90, 90])
    forecast = await weather.race_forecast(
        CIRCUIT, START.isoformat(), url=URL, now=START - timedelta(days=2),
        client=_client(payload),
    )
    assert [h["rain_chance_pct"] for h in forecast["hours"]] == [10, 20, 30, 40, 50]
    assert forecast["rain_chance_max_pct"] == 50
    assert forecast["summary"].startswith("Rain likely")
    assert "not an input" in forecast["note"]


@pytest.mark.parametrize("peak, opening", [(5, "Dry race"), (35, "Some chance"), (80, "Rain likely")])
def test_the_summary_follows_the_peak_chance(peak, opening):
    assert weather.describe(peak, 2.0).startswith(opening)


@pytest.mark.parametrize("now", [
    START - timedelta(days=17),       # beyond the forecast horizon
    START + timedelta(hours=4),       # race finished
])
async def test_nothing_outside_the_horizon(now):
    seen = []
    assert await weather.race_forecast(
        CIRCUIT, START, url=URL, now=now, client=_client(_payload([50] * 9), seen=seen),
    ) is None
    assert seen == []


async def test_nothing_without_a_location_or_a_url():
    assert await weather.race_forecast({"location": None}, START, url=URL) is None
    assert await weather.race_forecast(CIRCUIT, START, url="") is None


async def test_an_api_failure_is_none_and_not_cached():
    now = START - timedelta(days=1)
    assert await weather.race_forecast(
        CIRCUIT, START, url=URL, now=now, client=_client(status=503),
    ) is None
    later = await weather.race_forecast(
        CIRCUIT, START, url=URL, now=now, client=_client(_payload([10] * 9)),
    )
    assert later is not None


async def test_a_good_answer_is_cached():
    seen = []
    client = _client(_payload([10] * 9), seen=seen)
    now = START - timedelta(days=1)
    for _ in range(3):
        await weather.race_forecast(CIRCUIT, START, url=URL, now=now, client=client)
    assert len(seen) == 1


def test_bernie_is_told_it_is_not_in_the_model():
    facts = weather.weather_facts({"summary": "Dry race expected (5% peak chance of rain)."})
    assert "Dry race" in facts["race-day weather forecast (Open-Meteo)"]
    assert "not an input" in facts["weather and the forecast model"]
    assert weather.weather_facts(None) == {}
