"""The live feed starts itself before a race, once, and only with F1TV."""

import asyncio
from datetime import datetime, timedelta, timezone

from app.services import ingest_runner, live_autostart, live_feed
from app.services import scheduler as ingest_scheduler

START = datetime(2026, 11, 22, 4, 0, tzinfo=timezone.utc)
VEGAS = {"season": 2026, "round": 21, "race_name": "Las Vegas Grand Prix",
         "race_start_utc": START.isoformat()}


class Store:
    async def list_weekends(self, a, b):
        return [{"season": 2026, "round": 20, "race_start_utc": "2026-11-08T17:00:00Z"},
                VEGAS]

    async def recall_drivers(self, season):
        return {4: ("Lando Norris", "McLaren")}


class Feed(live_feed.LiveFeed):
    def __init__(self):
        super().__init__("unused")
        self.starts = []

    async def start(self, season, round_number, names, recording="", speed=0.0):
        self.starts.append((season, round_number, dict(names)))
        return {"running": True}


def tick(feed, at, lead=10):
    return asyncio.run(live_autostart.tick(Store(), feed, lead, now=at))


def setup_function():
    live_autostart._started.clear()


def test_due_opens_before_lights_out_and_closes_after_the_race():
    lead = timedelta(minutes=10)
    weekends = [VEGAS]
    assert live_autostart.due(weekends, START - timedelta(minutes=11), lead) is None
    assert live_autostart.due(weekends, START - timedelta(minutes=9), lead) == VEGAS
    assert live_autostart.due(weekends, START + timedelta(hours=2), lead) == VEGAS
    assert live_autostart.due(weekends, START + timedelta(hours=3), lead) is None


def test_it_starts_once_with_the_drivers_names(monkeypatch):
    monkeypatch.setattr(live_feed, "f1tv_connected", lambda: True)
    feed = Feed()
    assert tick(feed, START - timedelta(days=1)) is None
    tick(feed, START - timedelta(minutes=5))
    tick(feed, START)          # already started: an owner's Stop holds
    assert feed.starts == [(2026, 21, {4: "Lando Norris"})]


def test_without_f1tv_it_says_why_instead_of_starting(monkeypatch):
    monkeypatch.setattr(live_feed, "f1tv_connected", lambda: False)
    feed = Feed()
    tick(feed, START)
    assert feed.starts == [] and "connect F1TV" in feed.error
    # Connected later in the window: it starts then.
    monkeypatch.setattr(live_feed, "f1tv_connected", lambda: True)
    tick(feed, START + timedelta(minutes=20))
    assert len(feed.starts) == 1


def test_switched_off():
    feed = Feed()
    assert tick(feed, START, lead=0) is None and feed.starts == []


def test_the_scheduler_checks_every_minute():
    async def run():
        started = ingest_scheduler.start(_Runner(), interval_hours=6,
                                         live_tick=lambda: asyncio.sleep(0))
        try:
            job = started.get_job(ingest_scheduler.LIVE_JOB_ID)
            assert job is not None and job.misfire_grace_time is None
        finally:
            ingest_scheduler.shutdown()
    asyncio.run(run())


class _Runner:
    async def heal_season(self, *a, **k):
        return 0


def test_results_are_not_looked_for_until_the_race_can_be_over():
    assert not ingest_runner._has_run(START, now=START + timedelta(minutes=6),
                                      after=ingest_runner.RACE_RESULTS_AFTER)
    assert ingest_runner._has_run(START, now=START + timedelta(hours=2, minutes=1),
                                  after=ingest_runner.RACE_RESULTS_AFTER)


def test_practice_is_read_after_each_session_ends():
    fp1 = datetime(2026, 11, 20, 0, 30, tzinfo=timezone.utc)
    fp2 = datetime(2026, 11, 20, 4, 0, tzinfo=timezone.utc)

    class PracticeStore:
        async def list_weekends(self, a, b):
            return [dict(VEGAS, sessions={"Practice 1": fp1, "Practice 2": fp2,
                                          "Qualifying": START - timedelta(days=1)})]

    runner = ingest_runner.IngestRunner(source=None, store=PracticeStore())
    reads = []

    async def ingest_practice(expected):
        reads.append(expected.round)
        return 0 if len(reads) == 1 else 20      # first read finds nothing yet

    runner.ingest_practice = ingest_practice

    def at(moment):
        return asyncio.run(runner.ingest_weekend_practice(2026, now=moment))

    assert at(fp1 + timedelta(minutes=30)) == [] and reads == []     # still running
    assert at(fp1 + timedelta(hours=2)) == []                         # nothing yet: retried
    assert at(fp1 + timedelta(hours=2, minutes=30)) == ["2026-21"]
    assert at(fp1 + timedelta(hours=3)) == [] and len(reads) == 2      # FP1 done
    assert at(fp2 + timedelta(hours=2)) == ["2026-21"]                # after FP2, again
    assert at(START + timedelta(days=3)) == []                        # long gone
