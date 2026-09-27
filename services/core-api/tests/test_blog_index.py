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
#
# One card a race weekend. It was one card per entry, which gave a single
# grand prix five — qualifying, three forecasts and a result — each carrying
# the same photograph of the same circuit.


class _Background:
    """FastAPI's BackgroundTasks, reduced to what the handler uses."""

    def __init__(self):
        self.scheduled = []

    def add_task(self, func, *args, **kwargs):
        self.scheduled.append(func)


class _Llm:
    available = False


def _entry(round_number, kind, headline):
    from app.models.blog import BlogEntry

    return BlogEntry(
        entry_id="2026-{}-{}".format(round_number, kind.value),
        season=2026, round=round_number, race_name="Round {}".format(round_number),
        kind=kind, headline=headline, summary="", sources=["stored data"],
    )


def _wire_feed(monkeypatch, calendar, assemble):
    monkeypatch.setattr(blog, "_clients", lambda settings: (
        _Client({"/forward/calendar": calendar, "/circuits": {"imagery": []},
                 "/data/results": []}),
        _Client({}),
    ))
    monkeypatch.setattr(blog, "ServiceClient", lambda *a, **k: _Client({"/scores": []}))
    monkeypatch.setattr(blog, "_assemble", assemble)
    monkeypatch.setattr(blog, "Bernie", lambda llm: _Llm())


async def _no_summary(usage, season, round_number, entries):
    return ""


async def test_a_weekend_is_one_card_not_one_per_entry(monkeypatch):
    """A grand prix produced five cards, all with the same photograph of the
    same circuit. That is a timeline with the weekends taken out of it."""
    from app.models.blog import EntryKind

    async def assemble(settings, season, round_number):
        return [
            _entry(round_number, EntryKind.QUALIFYING, "Pole"),
            _entry(round_number, EntryKind.FORECAST, "Forecast"),
            _entry(round_number, EntryKind.RESULT, "Result"),
        ], "Round {}".format(round_number), "Circuit"

    _wire_feed(monkeypatch, [_weekend(15, -1)], assemble)
    monkeypatch.setattr(blog, "_cached_summary", _no_summary)

    items = await blog.feed(_Background(), limit=10, settings=Settings(),
                            llm=None, usage=None)

    assert len(items) == 1
    assert items[0].round == 15
    assert len(items[0].entries) == 3


async def test_the_feed_leads_with_the_most_recent_race(monkeypatch):
    from app.models.blog import EntryKind

    async def assemble(settings, season, round_number):
        return ([_entry(round_number, EntryKind.RESULT, "Result")],
                "Round {}".format(round_number), "Circuit")

    _wire_feed(monkeypatch, [_weekend(14, -21), _weekend(15, -1), _weekend(16, +7)],
               assemble)
    monkeypatch.setattr(blog, "_cached_summary", _no_summary)

    items = await blog.feed(_Background(), limit=10, settings=Settings(),
                            llm=None, usage=None)

    assert [i.round for i in items] == [15, 14]


async def test_a_race_that_has_not_run_is_not_in_the_feed(monkeypatch):
    async def assemble(settings, season, round_number):
        raise AssertionError("should not assemble a race that has not run")

    _wire_feed(monkeypatch, [_weekend(16, +7)], assemble)
    monkeypatch.setattr(blog, "_cached_summary", _no_summary)

    assert await blog.feed(_Background(), limit=10, settings=Settings(),
                           llm=None, usage=None) == []


async def test_one_broken_weekend_does_not_empty_the_feed(monkeypatch):
    from app.models.blog import EntryKind

    async def assemble(settings, season, round_number):
        if round_number == 15:
            raise RuntimeError("upstream is confused about this one")
        return ([_entry(14, EntryKind.RESULT, "Result")], "Round 14", "Circuit")

    _wire_feed(monkeypatch, [_weekend(14, -21), _weekend(15, -1)], assemble)
    monkeypatch.setattr(blog, "_cached_summary", _no_summary)

    items = await blog.feed(_Background(), limit=10, settings=Settings(),
                            llm=None, usage=None)

    assert [i.round for i in items] == [14]


async def test_the_request_never_waits_for_a_summary(monkeypatch):
    """Ten weekends is ten model calls on a cold cache, and the page showed a
    skeleton for the whole of it. A card without its summary renders; the
    writing happens after the response goes out."""
    from app.models.blog import EntryKind

    class _Available:
        available = True

    async def assemble(settings, season, round_number):
        return ([_entry(15, EntryKind.RESULT, "Result")], "Round 15", "Circuit")

    _wire_feed(monkeypatch, [_weekend(15, -1)], assemble)
    monkeypatch.setattr(blog, "Bernie", lambda llm: _Available())
    monkeypatch.setattr(blog, "_cached_summary", _no_summary)

    background = _Background()
    items = await blog.feed(background, limit=10, settings=Settings(),
                            llm=None, usage=None)

    assert items[0].summary == ""
    assert background.scheduled, "the summary was never scheduled"
