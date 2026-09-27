"""One Blog's front door.

The entries have always existed at /blog/{season}/{round}, reachable only by
already knowing a season and a round. There was no link to One Blog in the
masthead and no index behind one, so the part of the product described as a
highlight could not be navigated to.

What matters here is the ordering and what each card promises. An index that
leads with a race two months away, or that links to a weekend with nothing on
it, is worse than no index.
"""

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.routers import blog


NOW = datetime.now(timezone.utc)


class _Client:
    def __init__(self, routes):
        self._routes = routes

    async def get(self, path, params=None):
        for prefix, payload in self._routes.items():
            if path.startswith(prefix):
                return payload
        return None


def _weekend(rnd, days_from_now, name=None):
    start = NOW + timedelta(days=days_from_now)
    return {
        "season": 2026, "round": rnd,
        "race_name": name or "Round {} Grand Prix".format(rnd),
        "circuit": "Circuit {}".format(rnd), "country": "Country",
        "race_start_utc": start.isoformat().replace("+00:00", "Z"),
    }


def _wire(monkeypatch, calendar, scores=(), seasons=(2026, 2025)):
    monkeypatch.setattr(blog, "_clients", lambda settings: (
        _Client({"/forward/calendar": calendar, "/data/seasons": list(seasons)}),
        _Client({}),
    ))
    monkeypatch.setattr(
        blog, "ServiceClient",
        lambda *a, **k: _Client({"/scores": list(scores)}),
    )


async def test_the_record_leads_with_the_last_race_that_happened(monkeypatch):
    """Plain newest-first put round 23 at the top in September, two months
    before anyone could read anything about it."""
    _wire(monkeypatch, [
        _weekend(14, -21), _weekend(15, -1), _weekend(16, +7), _weekend(23, +60),
    ])

    index = await blog.index(None, Settings())

    assert [w.round for w in index.weekends] == [15, 14, 16, 23]


async def test_races_still_to_come_run_in_the_order_they_will_be_raced(monkeypatch):
    _wire(monkeypatch, [_weekend(23, +60), _weekend(16, +7), _weekend(17, +21)])

    index = await blog.index(None, Settings())

    assert [w.round for w in index.weekends] == [16, 17, 23]


async def test_a_race_that_has_run_is_marked_by_the_clock(monkeypatch):
    """By the calendar, not by what we hold. A race that finished an hour ago
    is in the past even if nothing has been ingested for it yet, and saying
    otherwise would have the index telling a different story from the page."""
    _wire(monkeypatch, [_weekend(15, -1), _weekend(16, +7)])

    index = await blog.index(None, Settings())
    by_round = {w.round: w for w in index.weekends}

    assert by_round[15].has_run is True
    assert by_round[16].has_run is False


async def test_a_scored_weekend_says_so(monkeypatch):
    """The card promises what there is to read. "Scored" is the difference
    between a forecast and a forecast that has been marked."""
    _wire(
        monkeypatch,
        [_weekend(14, -21), _weekend(15, -1)],
        scores=[{"season": 2026, "round": 15, "window": "final_grid"}],
    )

    index = await blog.index(None, Settings())
    by_round = {w.round: w for w in index.weekends}

    assert by_round[15].scored is True
    assert by_round[14].scored is False


async def test_an_empty_calendar_is_not_an_error(monkeypatch):
    """A season we hold nothing for renders an empty record, not a failure."""
    _wire(monkeypatch, [])

    index = await blog.index(2019, Settings())

    assert index.weekends == []
    assert index.season == 2019


async def test_a_downstream_outage_does_not_take_the_index_with_it(monkeypatch):
    """Scoring being down should cost the "scored" chips, not the page."""
    monkeypatch.setattr(blog, "_clients", lambda settings: (
        _Client({"/forward/calendar": [_weekend(15, -1)], "/data/seasons": [2026]}),
        _Client({}),
    ))

    class _Down:
        async def get(self, path, params=None):
            raise RuntimeError("scoring is down")

    monkeypatch.setattr(blog, "ServiceClient", lambda *a, **k: _Down())

    index = await blog.index(None, Settings())

    assert [w.round for w in index.weekends] == [15]
    assert index.weekends[0].scored is False


# ── The feed ─────────────────────────────────────────────────────────────────


async def test_the_feed_leads_with_the_most_recent_race(monkeypatch):
    """The index lists weekends; the feed lists what happened inside them. A
    reader arriving at a blog wants to see what there is to read."""
    from app.models.blog import BlogEntry, EntryKind

    def _entry(name):
        return BlogEntry(
            entry_id="2026-{}-result".format(name), season=2026, round=name,
            race_name="Round {}".format(name), kind=EntryKind.RESULT,
            headline="Round {} result".format(name), summary="",
            sources=["results"],
        )

    calendar = [_weekend(14, -21), _weekend(15, -1), _weekend(16, +7)]
    monkeypatch.setattr(blog, "_clients", lambda settings: (
        _Client({"/forward/calendar": calendar, "/circuits": {"imagery": []}}),
        _Client({}),
    ))

    async def _assemble(settings, season, round_number):
        return [_entry(round_number)], "Round {}".format(round_number), "Circuit"

    monkeypatch.setattr(blog, "_assemble", _assemble)

    items = await blog.feed(limit=10, settings=Settings())

    assert [i.round for i in items] == [15, 14]


async def test_a_race_that_has_not_run_is_not_in_the_feed(monkeypatch):
    """There is nothing to read about it yet, and a card that opens onto
    nothing is worse than no card."""
    monkeypatch.setattr(blog, "_clients", lambda settings: (
        _Client({"/forward/calendar": [_weekend(16, +7)], "/circuits": {}}),
        _Client({}),
    ))

    async def _assemble(settings, season, round_number):
        raise AssertionError("should not assemble a race that has not run")

    monkeypatch.setattr(blog, "_assemble", _assemble)

    assert await blog.feed(limit=10, settings=Settings()) == []


async def test_one_broken_weekend_does_not_empty_the_feed(monkeypatch):
    """A weekend whose data will not assemble costs its own cards and nothing
    else."""
    from app.models.blog import BlogEntry, EntryKind

    calendar = [_weekend(14, -21), _weekend(15, -1)]
    monkeypatch.setattr(blog, "_clients", lambda settings: (
        _Client({"/forward/calendar": calendar, "/circuits": {"imagery": []}}),
        _Client({}),
    ))

    async def _assemble(settings, season, round_number):
        if round_number == 15:
            raise RuntimeError("upstream is confused about this one")
        return [BlogEntry(
            entry_id="2026-14-result", season=2026, round=14,
            race_name="Round 14", kind=EntryKind.RESULT,
            headline="Round 14 result", summary="", sources=["results"],
        )], "Round 14", "Circuit"

    monkeypatch.setattr(blog, "_assemble", _assemble)

    items = await blog.feed(limit=10, settings=Settings())

    assert [i.round for i in items] == [14]
