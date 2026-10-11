"""Sprint forecasts: a separate race, forecast on its own grid and its own rules.

Every test here guards one way a sprint forecast could be quietly wrong while
looking perfectly well-formed: priced on the Grand Prix grid, counting ten
points places instead of eight, or turning up wherever a reader expected the
Grand Prix.
"""

import pytest

from app import dependencies
from app.models.schemas import (
    GRID_WINDOWS,
    MARKETS_BY_WINDOW,
    Event,
    LockWindow,
    Prediction,
    event_of,
    sprint_points_positions,
)
from app.services.ingestion_client import GridSlot
from app.services.model import race_distance_dnf
from app.services.predictor import GridRequired, Predictor
from tests.test_api import FakeClient, FakeStore

FIELD = [
    "Max Verstappen", "Lando Norris", "Charles Leclerc", "George Russell",
    "Oscar Piastri", "Lewis Hamilton", "Carlos Sainz", "Fernando Alonso",
    "Pierre Gasly", "Alex Albon",
]


def _slots(season, round_number, names):
    return [
        GridSlot(season=season, round=round_number, driver=name, team="T",
                 position=index + 1)
        for index, name in enumerate(names)
    ]


class SprintClient(FakeClient):
    """The Grand Prix grid and the sprint grid set deliberately different
    orders, so a forecast priced on the wrong one is visible."""

    def __init__(self, sprint_grid=True):
        super().__init__()
        self._sprint_grid = sprint_grid
        self.asked_for = []

    async def grid(self, season, round_number):
        self.asked_for.append("grid")
        return _slots(season, round_number, list(reversed(FIELD)))

    async def sprint_grid(self, season, round_number):
        self.asked_for.append("sprint_grid")
        return _slots(season, round_number, FIELD) if self._sprint_grid else []


def _predictor(client):
    return Predictor(client=client, store=FakeStore(), model=dependencies.get_model())


# ── Shape ────────────────────────────────────────────────────────────────────


def test_the_sprint_is_its_own_event_on_a_grid():
    assert event_of(LockWindow.SPRINT) is Event.SPRINT
    assert event_of(LockWindow.POST_QUALI) is Event.RACE
    assert LockWindow.SPRINT in GRID_WINDOWS


def test_it_publishes_what_the_grid_aware_grand_prix_windows_publish():
    assert MARKETS_BY_WINDOW[LockWindow.SPRINT] == MARKETS_BY_WINDOW[LockWindow.POST_QUALI]


def test_sprint_points_places_follow_the_season():
    assert sprint_points_positions(2021) == 3
    assert sprint_points_positions(2022) == 8
    assert sprint_points_positions(2026) == 8


def test_retirement_rate_scales_by_distance_not_linearly():
    assert race_distance_dnf(0.1, 1.0) == pytest.approx(0.1)
    # Surviving a third of the race: (0.9) ** (1/3).
    assert race_distance_dnf(0.1, 1 / 3) == pytest.approx(1 - 0.9 ** (1 / 3))
    assert race_distance_dnf(0.1, 1 / 3) < 0.1
    assert race_distance_dnf(0.0, 1 / 3) == 0.0


# ── Building ─────────────────────────────────────────────────────────────────


async def test_the_sprint_is_priced_on_the_sprint_grid():
    client = SprintClient()
    prediction, snapshot = await _predictor(client).build(
        season=2026, round_number=5, window=LockWindow.SPRINT
    )

    assert client.asked_for == ["sprint_grid"]
    grid = {d.driver: d.grid_position for d in snapshot.drivers}
    assert grid["Max Verstappen"] == 1 and grid["Alex Albon"] == 10
    favourite = max(prediction.driver_probabilities, key=lambda r: r.p_win)
    assert favourite.driver != "Alex Albon"


async def test_the_points_market_counts_eight_places_not_ten():
    """Summed over the field, p_points is the expected number of scorers:
    eight in a sprint. Ten would claim two places that score nothing."""
    prediction, _ = await _predictor(SprintClient()).build(
        season=2026, round_number=5, window=LockWindow.SPRINT
    )
    total = sum(row.p_points for row in prediction.driver_probabilities)

    assert prediction.points_positions == 8
    assert total == pytest.approx(8.0, abs=1e-9)


async def test_a_sprint_without_its_grid_waits_rather_than_borrowing_one():
    with pytest.raises(GridRequired) as exc:
        await _predictor(SprintClient(sprint_grid=False)).build(
            season=2026, round_number=5, window=LockWindow.SPRINT
        )
    assert "sprint qualifying" in str(exc.value)


async def test_the_sprint_grid_is_flagged_provisional():
    """No stewards' sprint grid is read, so a sprint penalty is not applied —
    which the forecast has to say rather than imply otherwise."""
    prediction, _ = await _predictor(SprintClient()).build(
        season=2026, round_number=5, window=LockWindow.SPRINT
    )
    assert prediction.data_quality.grid_is_provisional
    assert "sprint qualifying" in prediction.data_quality.notes


async def test_sprint_and_grand_prix_seeds_differ():
    sprint, _ = await _predictor(SprintClient()).build(
        season=2026, round_number=5, window=LockWindow.SPRINT
    )
    race, _ = await _predictor(SprintClient()).build(
        season=2026, round_number=5, window=LockWindow.POST_QUALI
    )
    assert sprint.seed != race.seed
    assert race.points_positions == 10


# ── Keeping it apart from the Grand Prix ─────────────────────────────────────


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *_):
        return self

    def limit(self, count):
        return _Cursor(self._rows[:count])

    def __aiter__(self):
        async def rows():
            for row in self._rows:
                yield row
        return rows()


class _Database:
    """Just enough of Mongo to run the store's real query: equality, ``$in``
    and ``$nin`` — so the filter under test is the one that ships."""

    def __init__(self):
        self.documents = []

    def __getitem__(self, _name):
        return self

    def find(self, query, projection=None):
        def matches(document):
            for field, wanted in query.items():
                value = document.get(field)
                if isinstance(wanted, dict):
                    if "$in" in wanted and value not in wanted["$in"]:
                        return False
                    if "$nin" in wanted and value in wanted["$nin"]:
                        return False
                elif value != wanted:
                    return False
            return True

        return _Cursor([dict(d) for d in self.documents if matches(d)])


async def test_the_store_serves_grand_prix_forecasts_unless_asked_for_the_sprint():
    """Scoring, the blog, Bernie and the last-race panel all ask for "the
    forecasts for round 5" and mean the Grand Prix; several fall back to the
    first row they get. A sprint forecast in that list would be read as one."""
    from app.services.storage import PredictionStore

    database = _Database()
    store = PredictionStore(database)
    for window in (LockWindow.POST_QUALI, LockWindow.SPRINT):
        prediction, _ = await _predictor(SprintClient()).build(
            season=2026, round_number=5, window=window
        )
        database.documents.append(prediction.model_dump(mode="json"))

    default = await store.list_predictions(2026, 5)
    sprint = await store.list_predictions(2026, 5, event=Event.SPRINT)
    both = await store.list_predictions(2026, 5, event=None)

    assert [p.window for p in default] == [LockWindow.POST_QUALI]
    assert [p.window for p in sprint] == [LockWindow.SPRINT]
    assert {p.window for p in both} == {LockWindow.POST_QUALI, LockWindow.SPRINT}


def test_a_prediction_stored_before_points_positions_existed_reads_as_ten():
    stored = Prediction(
        prediction_id="old", season=2025, round=3, window=LockWindow.POST_QUALI,
        locked_at="2025-03-01T00:00:00Z", model_version="v", seed=1,
        feature_snapshot_ref="s",
    )
    assert stored.points_positions == 10
