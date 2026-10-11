"""Paddock news in Bernie, One Blog and the public route.

The property every test here protects is the same one: a headline is the
outlet's report, never ours. It reaches Bernie labelled with who published it,
it reaches the blog with a link to the original and a line saying we have not
checked it, and it is never retold in our voice.
"""

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.models.blog import BlogEntry, EntryKind
from app.routers import bernie as bernie_router
from app.routers import blog
from app.routers import news as news_router
from app.services import blog as builder
from app.services.bernie import (
    MAX_NEWS_ITEMS,
    NEWS_KEY,
    SYSTEM_PROMPT,
    news_facts,
)


def _story(n, outlet="Autosport", **extra):
    item = {
        "id": "id{}".format(n),
        "source": outlet.lower(),
        "outlet": outlet,
        "title": "Story {}".format(n),
        "url": "https://example.com/{}".format(n),
        "summary": "Summary {}".format(n),
        "published_at": "2026-10-0{}T10:00:00+00:00".format(min(n, 9) or 1),
        "published_estimated": False,
    }
    item.update(extra)
    return item


def _titles(facts):
    return [line.split(": ", 1)[1].split(" —")[0] for line in facts[NEWS_KEY]]


# ── Bernie's facts ───────────────────────────────────────────────────────────


def test_every_headline_names_its_outlet_and_date():
    facts = news_facts([], [_story(8, outlet="Formula1.com")])
    [line] = facts[NEWS_KEY]
    assert line.startswith("Formula1.com, 8 Oct: Story 8")
    assert "Summary 8" in line


def test_the_label_says_these_are_reports():
    assert "not verified" in NEWS_KEY


def test_question_matches_come_before_the_newest():
    facts = news_facts([_story(1)], [_story(5), _story(1)])
    assert _titles(facts) == ["Story 1", "Story 5"]


def test_the_pack_is_capped():
    facts = news_facts([], [_story(n) for n in range(1, 30)])
    assert len(facts[NEWS_KEY]) == MAX_NEWS_ITEMS


def test_no_news_is_no_heading():
    """An empty heading invites Bernie to fill it."""
    assert news_facts([], []) == {}


def test_an_estimated_date_is_not_passed_off_as_the_outlets():
    facts = news_facts([], [_story(3, published_estimated=True)])
    assert "first seen 3 Oct" in facts[NEWS_KEY][0]


def test_long_summaries_are_trimmed_in_the_prompt():
    facts = news_facts([], [_story(2, summary="word " * 200)])
    assert len(facts[NEWS_KEY][0]) < 300


def test_the_system_prompt_forbids_presenting_news_as_fact():
    assert "Attribute every one you use" in SYSTEM_PROMPT
    assert "The forecast does not use the news" in SYSTEM_PROMPT


class _Client:
    def __init__(self, routes=None, fail=False):
        self._routes = routes or {}
        self._fail = fail
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, params))
        if self._fail:
            from app.services.downstream import DownstreamUnavailable
            raise DownstreamUnavailable("ingestion", "down")
        for prefix, payload in self._routes.items():
            if path.startswith(prefix):
                return payload
        return None


async def test_bernie_gets_race_week_news_scoped_to_the_round(monkeypatch):
    client = _Client({
        "/news/weekend/2026/18": {"items": [_story(5)]},
        "/news/search": [_story(2)],
    })
    monkeypatch.setattr(bernie_router, "ServiceClient", lambda *a, **k: client)

    facts = await bernie_router._news_facts(Settings(), "any upgrades?", 2026, 18)

    assert _titles(facts) == ["Story 2", "Story 5"]
    search = next(params for path, params in client.calls if path == "/news/search")
    assert search["season"] == 2026 and search["round"] == 18


async def test_unreachable_news_is_said_not_swallowed(monkeypatch):
    monkeypatch.setattr(bernie_router, "ServiceClient", lambda *a, **k: _Client(fail=True))
    facts = await bernie_router._news_facts(Settings(), "any upgrades?", 2026, 18)
    assert NEWS_KEY not in facts
    assert "could not be loaded" in facts["note on paddock news"]


# ── One Blog ─────────────────────────────────────────────────────────────────


WEEK = {
    "since": "2026-10-05T12:00:00+00:00",
    "until": "2026-10-12T12:00:00+00:00",
    "items": [_story(6, outlet="Formula1.com"), _story(7), _story(8)],
}


def test_the_news_entry_links_every_headline_and_says_we_did_not_check_it():
    entry = builder.news_entry(2026, 18, "Singapore Grand Prix", WEEK)
    assert entry.kind == EntryKind.NEWS
    assert entry.headline == "3 stories from the paddock in race week"
    assert "have not checked them" in entry.summary
    assert "forecast does not use them" in entry.summary
    assert "between 5 Oct and 12 Oct" in entry.summary
    assert all(row["url"].startswith("https://") for row in entry.table)
    assert entry.sources == ["Autosport RSS feed", "Formula1.com RSS feed"]
    assert entry.facts[0].label == "Formula1.com"


def test_a_week_with_no_news_has_no_entry():
    assert builder.news_entry(2026, 18, "", {"items": []}) is None
    assert builder.news_entry(2026, 18, "", None) is None


def test_the_news_entry_leads_the_timeline():
    news = builder.news_entry(2026, 18, "", WEEK)
    result = BlogEntry(
        entry_id="r", season=2026, round=18, kind=EntryKind.RESULT,
        headline="Result", sources=["stored"],
    )
    weekend = builder.assemble(2026, 18, "", "", [result, news], True)
    assert [e.kind for e in weekend.entries] == [EntryKind.NEWS, EntryKind.RESULT]


async def test_news_is_never_narrated():
    """Prose over headlines is a report retold in our voice."""

    class _Bernie:
        available = True

        async def explain(self, **_):
            raise AssertionError("news must not be narrated")

    entry = builder.news_entry(2026, 18, "", WEEK)
    assert (await builder.narrate(_Bernie(), entry)).narrative is None


class _Background:
    def __init__(self):
        self.scheduled = []

    def add_task(self, func, *args, **kwargs):
        self.scheduled.append(args)


def _past_weekend():
    now = datetime.now(timezone.utc)
    return now.year, {
        "season": now.year, "round": 18, "race_name": "R18", "circuit": "C",
        "race_start_utc": (now - timedelta(days=1)).isoformat(),
    }


def _wire_feed(monkeypatch, weekend, assemble):
    monkeypatch.setattr(blog, "_clients", lambda settings: (
        _Client({"/forward/calendar": [weekend], "/circuits": {"imagery": []},
                 "/data/results": []}),
        _Client(),
    ))
    monkeypatch.setattr(blog, "ServiceClient", lambda *a, **k: _Client({"/scores": []}))
    monkeypatch.setattr(blog, "_assemble", assemble)


async def test_the_feed_summary_ignores_the_news(monkeypatch):
    """Headlines arrive all week; the card's summary is about the race."""
    season, weekend = _past_weekend()
    result = BlogEntry(
        entry_id="r", season=season, round=18, kind=EntryKind.RESULT,
        headline="Result", sources=["stored"],
    )
    news = builder.news_entry(season, 18, "", WEEK)

    async def assemble(settings, season, round_number):
        return [news, result], "R18", "C"

    seen = {}

    async def cached(usage, season, round_number, entries):
        seen["entries"] = entries
        return ""

    class _Available:
        available = True

    _wire_feed(monkeypatch, weekend, assemble)
    monkeypatch.setattr(blog, "_cached_summary", cached)
    monkeypatch.setattr(blog, "Bernie", lambda llm: _Available())

    background = _Background()
    [item] = await blog.feed(background, limit=10, settings=Settings(),
                             llm=None, usage=None)

    assert [e.kind for e in seen["entries"]] == [EntryKind.RESULT]
    assert item.headline == "Result"
    assert "Paddock news" in [e.kind_label for e in item.entries]
    assert [e.kind for e in background.scheduled[0][-1]] == [EntryKind.RESULT]


async def test_a_weekend_with_only_news_is_not_a_card(monkeypatch):
    season, weekend = _past_weekend()

    async def assemble(settings, season, round_number):
        return [builder.news_entry(season, 18, "", WEEK)], "", ""

    _wire_feed(monkeypatch, weekend, assemble)

    assert await blog.feed(_Background(), limit=10, settings=Settings(),
                           llm=None, usage=None) == []


# ── The public route ─────────────────────────────────────────────────────────


async def test_the_public_route_carries_the_attribution(monkeypatch):
    client = _Client({"/news": [_story(1), {"title": "no link", "id": "x"}]})
    monkeypatch.setattr(news_router, "ServiceClient", lambda *a, **k: client)

    page = await news_router.latest(limit=30, source=None, settings=Settings())

    assert [item.title for item in page.items] == ["Story 1"]
    assert "not checked" in page.attribution


async def test_the_weekend_route_returns_its_window(monkeypatch):
    monkeypatch.setattr(news_router, "ServiceClient", lambda *a, **k: _Client({
        "/news/weekend/2026/18": WEEK,
    }))
    page = await news_router.weekend(2026, 18, limit=12, settings=Settings())
    assert page.since == WEEK["since"]
    assert len(page.items) == 3
