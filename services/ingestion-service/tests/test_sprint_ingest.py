"""Sprint weekends: two more sessions, found by name and stored apart.

The sprint lock window waits on sprint qualifying and scoring waits on the
sprint, so both have to arrive on their own. And both must stay out of the
``results`` and ``qualifying`` collections, which everything else reads as
"the Grand Prix".
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from app.models.schemas import QualifyingRow, ResultRow, sprint_session_names
from app.services.fastf1_source import SessionUnavailableError
from app.services.ingest_runner import IngestRunner
from app.services.storage import SPRINT_QUALIFYING, SPRINT_RESULTS

NOW = datetime.now(timezone.utc)
PAST = NOW - timedelta(hours=2)
FUTURE = NOW + timedelta(hours=20)


def _weekend(round_number: int, sessions: Dict[str, datetime]) -> dict:
    return {
        "season": 2026,
        "round": round_number,
        "race_name": "Round {}".format(round_number),
        "circuit": "Somewhere",
        "sessions": sessions,
    }


class FakeStore:
    def __init__(self, weekends: List[dict]) -> None:
        self.weekends = weekends
        self.rows: Dict[str, List[dict]] = {}
        self.saved: List[tuple] = []

    async def list_weekends(self, first, last):
        return self.weekends

    async def count_matching(self, collection, where):
        return len(self._matching(collection, where))

    async def find_rows(self, collection, where=None, limit=0, sort=None):
        return self._matching(collection, where or {})

    async def save_session_rows(self, collection, season, round_number, rows):
        self.saved.append((collection, season, round_number))
        self.rows.setdefault(collection, []).extend(
            row.model_dump(mode="json") for row in rows
        )
        return len(rows)

    def _matching(self, collection, where):
        return [
            row for row in self.rows.get(collection, [])
            if all(row.get(k) == v for k, v in where.items())
        ]


class FakeSource:
    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.sprint_error = None

    def load_qualifying(self, expected, session_name="Q"):
        self.calls.append(("quali", expected.round, session_name))
        return [
            QualifyingRow(
                id="q{}{}".format(expected.round, i), season=expected.season,
                round=expected.round, driver="Driver {}".format(i), position=i,
            )
            for i in range(1, 4)
        ]

    def load_sprint_results(self, expected, session_name="Sprint"):
        self.calls.append(("sprint", expected.round, session_name))
        if self.sprint_error:
            raise self.sprint_error
        return [
            ResultRow(
                id="s{}{}".format(expected.round, i), season=expected.season,
                round=expected.round, driver="Driver {}".format(i), position=i,
                classified_position=str(i),
            )
            for i in range(1, 4)
        ]


def _runner(weekends):
    store, source = FakeStore(weekends), FakeSource()
    return IngestRunner(source=source, store=store), store, source


def test_session_names_follow_each_seasons_format():
    """The 2021 sprint was itself called "Sprint Qualifying"; from 2024 that
    name means the session that sets the sprint grid. Reading it the wrong way
    round would forecast a sprint on its own result."""
    assert sprint_session_names({"Sprint Qualifying": NOW, "Qualifying": NOW}) == (
        None, "Sprint Qualifying",
    )
    assert sprint_session_names({"Sprint": NOW, "Qualifying": NOW}) == (None, "Sprint")
    assert sprint_session_names({"Sprint Shootout": NOW, "Sprint": NOW}) == (
        "Sprint Shootout", "Sprint",
    )
    assert sprint_session_names({"Sprint Qualifying": NOW, "Sprint": NOW}) == (
        "Sprint Qualifying", "Sprint",
    )
    assert sprint_session_names({"Qualifying": NOW, "Race": NOW}) == (None, None)


async def test_sprint_qualifying_lands_before_the_sprint_and_apart_from_qualifying():
    runner, store, source = _runner(
        [_weekend(6, {"Sprint Qualifying": PAST, "Sprint": FUTURE, "Race": FUTURE})]
    )

    outcome = await runner.ingest_sprint_sessions(2026)

    assert source.calls == [("quali", 6, "Sprint Qualifying")]
    assert store.saved == [(SPRINT_QUALIFYING, 2026, 6)]
    assert outcome["ingested"] and not outcome["failed"]


async def test_each_session_is_fetched_once():
    runner, store, source = _runner(
        [_weekend(6, {"Sprint Qualifying": PAST, "Sprint": PAST, "Race": FUTURE})]
    )

    await runner.ingest_sprint_sessions(2026)
    await runner.ingest_sprint_sessions(2026)

    assert source.calls == [
        ("quali", 6, "Sprint Qualifying"),
        ("sprint", 6, "Sprint"),
    ]
    assert store.saved == [(SPRINT_QUALIFYING, 2026, 6), (SPRINT_RESULTS, 2026, 6)]


async def test_a_grand_prix_weekend_is_left_alone():
    runner, _, source = _runner(
        [_weekend(7, {"Qualifying": PAST, "Race": PAST})]
    )
    await runner.ingest_sprint_sessions(2026)
    assert source.calls == []


async def test_an_unpublished_sprint_result_waits_rather_than_failing():
    runner, store, source = _runner(
        [_weekend(6, {"Sprint": PAST, "Race": FUTURE})]
    )
    source.sprint_error = SessionUnavailableError("not yet")

    outcome = await runner.ingest_sprint_sessions(2026)

    assert outcome["waiting"] == ["2026-6 Sprint"]
    assert not outcome["failed"]
    assert store.saved == []


async def test_a_finished_sprints_grid_is_only_fetched_for_a_backfill():
    """The scheduled job has no use for a grid it can no longer forecast on;
    the backtest needs exactly that grid."""
    weekends = [_weekend(6, {"Sprint Shootout": PAST, "Sprint": PAST})]
    runner, store, source = _runner(weekends)
    store.rows[SPRINT_RESULTS] = [{"season": 2026, "round": 6, "position": 1}]

    await runner.ingest_sprint_sessions(2026)
    assert source.calls == []

    await runner.ingest_sprint_sessions(2026, include_finished=True)
    assert source.calls == [("quali", 6, "Sprint Shootout")]


def test_sprint_qualifying_order_is_rebuilt_from_its_laps():
    """Upstream never classifies sprint qualifying, so every row arrives with
    no position. The knockout rule recovers it: the last segment reached
    ranks first, then the best counting lap within it."""
    from datetime import timedelta as td

    import pandas as pd

    from app.services.transforms import knockout_classification

    def laps(rows):
        return pd.DataFrame(
            [{"DriverNumber": n, "LapTime": td(seconds=t), "Deleted": d}
             for n, t, d in rows]
        )

    order = knockout_classification([
        laps([("1", 91.0, False), ("4", 90.5, False), ("16", 92.0, False),
              ("44", 93.0, False)]),
        laps([("1", 90.2, False), ("4", 90.1, False), ("16", 90.9, False)]),
        # 4's quicker lap was deleted for track limits and does not count.
        laps([("1", 89.8, False), ("4", 89.5, True), ("4", 89.9, False)]),
    ])

    assert {n: p for n, (p, _) in order.items()} == {
        "1": 1, "4": 2, "16": 3, "44": 4,
    }
    assert order["4"][1] == [90.5, 90.1, 89.9]


def test_a_driver_without_a_lap_in_their_last_segment_goes_behind_it():
    from datetime import timedelta as td

    import pandas as pd

    from app.services.transforms import knockout_classification

    q1 = pd.DataFrame([
        {"DriverNumber": "10", "LapTime": td(seconds=91), "Deleted": False},
        {"DriverNumber": "22", "LapTime": pd.NaT, "Deleted": False},
        {"DriverNumber": "31", "LapTime": td(seconds=92), "Deleted": False},
    ])
    order = knockout_classification([q1, None, None])
    assert [n for n, _ in sorted(order.items(), key=lambda kv: kv[1][0])] == [
        "10", "31", "22",
    ]
