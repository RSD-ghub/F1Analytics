"""Paddock news: reading feeds safely, and keeping a report labelled as one.

The feeds are someone else's XML rendered on our public page, so most of what
is tested here is what must *not* come through: markup, script-bearing links,
tracking codes that duplicate a story, and dates that would pin an item to the
top of the list for ever.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.news import source
from app.news.source import (
    Feed,
    FeedUnavailable,
    NewsItem,
    canonical_url,
    clean_text,
    item_id,
    parse_feed,
)
from app.news.store import NEWS, NewsStore, weekend_window

FEED = Feed("autosport", "Autosport", "https://example.com/rss")
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Autosport F1</title>
    <item>
      <title>Team brings floor upgrade to Singapore</title>
      <link>https://example.com/f1/news/floor-upgrade/?utm_source=rss&amp;utm_medium=feed</link>
      <description>&lt;p&gt;The team will run a revised floor &lt;b&gt;this weekend&lt;/b&gt;.&lt;/p&gt;</description>
      <pubDate>Wed, 08 Oct 2026 14:30:00 +0100</pubDate>
    </item>
    <item>
      <title>Undated story</title>
      <link>https://example.com/f1/news/undated/</link>
      <description>No date on this one.</description>
    </item>
    <item>
      <title>Scripted link</title>
      <link>javascript:alert(1)</link>
      <pubDate>Wed, 08 Oct 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title></title>
      <link>https://example.com/f1/news/no-title/</link>
    </item>
    <item>
      <title>Team brings floor upgrade to Singapore</title>
      <link>https://example.com/f1/news/floor-upgrade/?utm_campaign=x</link>
      <pubDate>Wed, 08 Oct 2026 14:30:00 +0100</pubDate>
    </item>
    <item>
      <title>From the future</title>
      <link>https://example.com/f1/news/future/</link>
      <pubDate>Fri, 01 Jan 2027 00:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Formula1.com</title>
  <entry>
    <title>Official: calendar confirmed</title>
    <link rel="self" href="https://example.org/self"/>
    <link rel="alternate" href="https://example.org/latest/article/calendar"/>
    <summary>The calendar for next season is confirmed.</summary>
    <published>2026-10-07T09:15:00Z</published>
  </entry>
</feed>
"""


# ── Parsing ──────────────────────────────────────────────────────────────────


def test_rss_items_are_parsed_with_outlet_and_utc_time():
    items = parse_feed(RSS, FEED, now=NOW)
    upgrade = next(i for i in items if i.title.startswith("Team brings"))
    assert upgrade.outlet == "Autosport"
    assert upgrade.source == "autosport"
    assert upgrade.published_at == datetime(2026, 10, 8, 13, 30, tzinfo=timezone.utc)
    assert upgrade.published_estimated is False


def test_markup_is_stripped_from_summaries():
    upgrade = parse_feed(RSS, FEED, now=NOW)[0]
    assert upgrade.summary == "The team will run a revised floor this weekend ."


def test_tracking_parameters_do_not_duplicate_a_story():
    """Two links to one article, differing only in campaign codes, are one item."""
    items = parse_feed(RSS, FEED, now=NOW)
    assert [i.title for i in items].count("Team brings floor upgrade to Singapore") == 1
    assert "utm_" not in items[0].url


def test_a_javascript_link_never_reaches_the_page():
    items = parse_feed(RSS, FEED, now=NOW)
    assert all(i.url.startswith("https://") for i in items)
    assert "Scripted link" not in [i.title for i in items]


def test_an_item_without_a_title_is_dropped():
    assert all(i.title for i in parse_feed(RSS, FEED, now=NOW))


def test_an_undated_item_is_marked_as_estimated():
    undated = next(i for i in parse_feed(RSS, FEED, now=NOW) if i.title == "Undated story")
    assert undated.published_at == NOW
    assert undated.published_estimated is True


def test_a_feed_with_no_dates_keeps_the_outlets_own_order():
    """Formula1.com publishes no pubDate at all, so every item was landing on
    one instant, tied and ordered arbitrarily among themselves."""
    payload = b"""<rss><channel>
        <item><title>Newest</title><link>https://example.com/1</link></item>
        <item><title>Middle</title><link>https://example.com/2</link></item>
        <item><title>Oldest</title><link>https://example.com/3</link></item>
        </channel></rss>"""
    items = parse_feed(payload, FEED, now=NOW)

    assert [i.title for i in items] == ["Newest", "Middle", "Oldest"]
    stamps = [i.published_at for i in items]
    assert stamps == sorted(stamps, reverse=True), "feed order was lost"
    assert len(set(stamps)) == 3, "undated items are still tied"
    assert all(i.published_estimated for i in items)
    # Seconds apart, not hours: the spacing breaks a tie and must not read as
    # a real publication time.
    assert stamps[0] - stamps[-1] < timedelta(minutes=1)


def test_a_dated_item_is_not_shifted_by_an_undated_one():
    """The spacing applies to undated items only."""
    upgrade = next(
        i for i in parse_feed(RSS, FEED, now=NOW) if i.title.startswith("Team brings")
    )
    assert upgrade.published_at == datetime(2026, 10, 8, 13, 30, tzinfo=timezone.utc)


def test_a_date_in_the_future_is_not_trusted():
    """Otherwise it would sit at the top of every list until 2027."""
    future = next(i for i in parse_feed(RSS, FEED, now=NOW) if i.title == "From the future")
    assert NOW - timedelta(minutes=1) < future.published_at <= NOW
    assert future.published_estimated is True


def test_atom_uses_the_alternate_link():
    [entry] = parse_feed(ATOM, Feed("formula1", "Formula1.com", "x"), now=NOW)
    assert entry.url == "https://example.org/latest/article/calendar"
    assert entry.published_at == datetime(2026, 10, 7, 9, 15, tzinfo=timezone.utc)
    assert entry.summary == "The calendar for next season is confirmed."


def test_rss_guid_permalink_stands_in_for_a_missing_link():
    payload = b"""<rss><channel><item><title>T</title>
        <guid isPermaLink="true">https://example.com/a</guid></item>
        <item><title>U</title><guid isPermaLink="false">abc-123</guid></item>
        </channel></rss>"""
    items = parse_feed(payload, FEED, now=NOW)
    assert [i.url for i in items] == ["https://example.com/a"]


def test_garbage_is_refused_not_stored():
    with pytest.raises(FeedUnavailable):
        parse_feed(b"<html><body>Access denied</body></html>", FEED, now=NOW)
    with pytest.raises(FeedUnavailable):
        parse_feed(b"not xml at all", FEED, now=NOW)


def test_encoded_markup_comes_out_as_text_not_tags():
    """Tags are stripped before entities are decoded, never after."""
    assert clean_text("&lt;script&gt;x&lt;/script&gt;") == "<script>x</script>"
    assert clean_text("<script>x</script>") == "x"


def test_long_summaries_are_cut_on_a_word():
    text = clean_text("word " * 200, limit=40)
    assert len(text) <= 41
    assert text.endswith("…")
    assert not text[:-1].endswith(" ")


def test_canonical_url_normalises_host_and_trailing_slash():
    assert canonical_url("HTTPS://Example.COM/a/b/?utm_source=x&id=7#frag") == (
        "https://example.com/a/b?id=7"
    )
    assert canonical_url("ftp://example.com/a") is None
    assert canonical_url("") is None
    assert item_id("https://example.com/a") == item_id("https://example.com/a")


# ── The weekend window ───────────────────────────────────────────────────────


def test_a_race_week_runs_monday_to_monday():
    race = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)  # a Sunday
    since, until = weekend_window(race)
    assert since.strftime("%A") == "Monday"
    assert until.strftime("%A") == "Monday"
    assert since < race < until


def test_a_naive_race_start_is_read_as_utc():
    since, _ = weekend_window(datetime(2026, 10, 11, 12, 0))
    assert since.tzinfo is not None


# ── Refreshing ───────────────────────────────────────────────────────────────


class _MemoryStore:
    def __init__(self):
        self.saved = []
        self.pruned_before = None

    async def save(self, items):
        self.saved.extend(items)
        return len(items)

    async def prune(self, before):
        self.pruned_before = before
        return 0


async def test_one_outlet_down_does_not_cost_the_other(monkeypatch):
    good = Feed("good", "Good", "https://good")
    bad = Feed("bad", "Bad", "https://bad")

    async def fake_fetch(feed, timeout_seconds=20.0):
        if feed is bad:
            raise FeedUnavailable("403")
        return parse_feed(RSS, feed, now=NOW)

    monkeypatch.setattr(source, "fetch_feed", fake_fetch)
    store = _MemoryStore()
    summary = await source.refresh(store, feeds=[bad, good], retention_days=60)

    assert summary.feeds["bad"]["error"] == "403"
    assert summary.feeds["good"]["new"] == len(store.saved) > 0
    assert store.pruned_before is not None
    assert source.last_refresh is summary


# ── Storage ──────────────────────────────────────────────────────────────────


class _Result:
    upserted_count = 1


class _Collection:
    def __init__(self):
        self.operations = []

    async def bulk_write(self, operations, ordered=False):
        self.operations = operations
        return _Result()


class _Database(dict):
    def __missing__(self, key):
        self[key] = _Collection()
        return self[key]


def _item(estimated):
    return NewsItem(
        item_id="abc", source="autosport", outlet="Autosport", title="T",
        url="https://example.com/a", summary="S", published_at=NOW,
        published_estimated=estimated, fetched_at=NOW,
    )


async def test_a_dated_item_keeps_the_outlets_time_current():
    database = _Database()
    await NewsStore(database).save([_item(estimated=False)])
    [op] = database[NEWS].operations
    assert op._doc["$set"]["published_at"] == NOW
    assert "published_at" not in op._doc["$setOnInsert"]


async def test_an_undated_item_is_timestamped_once_and_never_again():
    """Refreshing an estimate on every pass floats it to the top for ever."""
    database = _Database()
    await NewsStore(database).save([_item(estimated=True)])
    [op] = database[NEWS].operations
    assert "published_at" not in op._doc["$set"]
    assert op._doc["$setOnInsert"]["published_at"] == NOW
    assert op._doc["$setOnInsert"]["published_estimated"] is True


async def test_saving_nothing_touches_nothing():
    database = _Database()
    assert await NewsStore(database).save([]) == 0
    assert NEWS not in database
