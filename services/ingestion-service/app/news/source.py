"""Paddock news, read from the outlets' own RSS feeds.

**RSS rather than a news API.** Formula1.com and Autosport both publish feeds
that need no key, no account and no contract, and a feed is the outlet saying
"take these". A news API would add a credential to rotate and a bill to watch
for a panel of headlines, and scraping article pages would be taking what was
not offered.

**Headlines, not articles.** What is kept is the title, the feed's own short
summary, the link and the time. The article stays on the outlet's site and the
reader goes there for it. That keeps this on the right side of what a feed
grants, and it keeps Bernie honest: he is handed a headline and knows it is a
headline, rather than a scraped body he could quote as if he had read it.

**A report is not a fact.** Everything stored here is labelled with the outlet
that published it, and nothing downstream may present it without that label.
The forecast does not read this collection at all — news is context for a
reader, not a feature for the model.
"""

import hashlib
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)

BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125"

#: A feed is a few dozen items and well under 200 KB. Anything far larger is not
#: the feed we asked for, and reading it whole would be the outlet's CDN
#: deciding how much memory this process spends.
MAX_FEED_BYTES = 2_000_000

#: Characters of feed summary kept. Enough for the standfirst an outlet writes
#: under its headline; short enough that a pack of eight items does not crowd
#: the forecast out of Bernie's prompt.
MAX_SUMMARY_CHARS = 320

#: A publication time further ahead than this is a feed bug, not an embargo.
#: Kept as-is it would sit at the top of every "latest" list for days.
FUTURE_TOLERANCE = timedelta(hours=1)

#: Gap between consecutive undated items from one feed. Enough to keep the
#: outlet's own order; too small to read as a real publication time.
UNDATED_SPACING = timedelta(seconds=1)

_ATOM = "{http://www.w3.org/2005/Atom}"
_TAG = re.compile(r"<[^>]+>")

#: Query parameters that identify a campaign rather than an article. Stripped
#: so the same story shared with two tracking codes is stored once.
_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|cmpid$|icid$)", re.IGNORECASE)


@dataclass(frozen=True)
class Feed:
    key: str
    outlet: str
    url: str


#: The outlets read by default. The Formula1.com feed is the sport's own and
#: carries the official announcements; Autosport is the long-standing trade
#: paper and carries the paddock reporting the official site does not.
FEEDS: Sequence[Feed] = (
    Feed("formula1", "Formula1.com", "https://www.formula1.com/en/latest/all.xml"),
    Feed("autosport", "Autosport", "https://www.autosport.com/rss/f1/news/"),
)


class FeedUnavailable(RuntimeError):
    """A feed could not be fetched or could not be read as RSS or Atom."""


@dataclass
class NewsItem:
    """One headline, as the outlet published it."""

    item_id: str
    source: str
    outlet: str
    title: str
    url: str
    summary: str
    published_at: datetime
    #: True when the feed gave no usable date and ``published_at`` is the time
    #: we first saw the item. Kept so nothing downstream reads an estimate as
    #: the outlet's own timestamp.
    published_estimated: bool = False
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def canonical_url(url: str) -> Optional[str]:
    """The article's address with tracking removed, or None if it is not a web link.

    Only http and https survive. The link is rendered as an anchor on a public
    page, and a ``javascript:`` URL in a compromised or spoofed feed would
    otherwise be one click from running in our origin.
    """
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    query = urlencode([
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _TRACKING.match(key)
    ])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def item_id(url: str) -> str:
    """Stable id for an article: a hash of its canonical address.

    Keyed on the link rather than the feed's ``guid`` because the same story can
    appear in more than one feed, and the link is the one thing both agree on.
    """
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]


def clean_text(raw: Optional[str], limit: Optional[int] = None) -> str:
    """Plain text from a feed field that may carry HTML and entities.

    Feeds put markup in descriptions — paragraphs, image tags, "Read more"
    links. None of it is wanted, and rendering it would mean trusting an
    outlet's HTML on our page. Tags are removed before entities are decoded, so
    an encoded ``&lt;script&gt;`` comes out as visible text, never as a tag.
    """
    text = _TAG.sub(" ", raw or "")
    text = html.unescape(text)
    text = " ".join(text.split())
    if limit and len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:-")
        text = cut + "…"
    return text


def _parse_date(raw: Optional[str]) -> Optional[datetime]:
    """RFC 822 (RSS) or ISO 8601 (Atom), always returned in UTC."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    parsed: Optional[datetime] = None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _text(element: Optional[ElementTree.Element], *paths: str) -> str:
    if element is None:
        return ""
    for path in paths:
        found = element.find(path)
        if found is not None and (found.text or "").strip():
            return found.text
    return ""


def _atom_link(entry: ElementTree.Element) -> str:
    """The entry's article link: ``rel="alternate"`` or no rel at all."""
    for link in entry.findall(_ATOM + "link"):
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href")
    return ""


def _guid_link(item: ElementTree.Element) -> str:
    """A ``<guid isPermaLink="true">`` is the article link when ``<link>`` is absent."""
    guid = item.find("guid")
    if guid is None or not (guid.text or "").strip():
        return ""
    if guid.get("isPermaLink", "true").lower() == "false":
        return ""
    return guid.text


def parse_feed(payload: bytes, feed: Feed, now: Optional[datetime] = None) -> List[NewsItem]:
    """Headlines from an RSS 2.0 or Atom document.

    An item without a usable title or a web link is dropped rather than stored
    half-formed: a headline nobody can click through to is not something a
    reader can check, and checking is the point of showing the source.
    """
    now = now or datetime.now(timezone.utc)
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise FeedUnavailable("{} is not valid XML: {}".format(feed.outlet, exc)) from exc

    if root.tag == _ATOM + "feed":
        raw_items = [
            (
                _text(entry, _ATOM + "title"),
                _atom_link(entry),
                _text(entry, _ATOM + "summary", _ATOM + "content"),
                _text(entry, _ATOM + "published", _ATOM + "updated"),
            )
            for entry in root.findall(_ATOM + "entry")
        ]
    elif root.tag == "rss" or root.find("channel") is not None:
        raw_items = [
            (
                _text(item, "title"),
                _text(item, "link") or _guid_link(item),
                _text(item, "description"),
                _text(item, "pubDate", "{http://purl.org/dc/elements/1.1/}date"),
            )
            for item in root.iter("item")
        ]
    else:
        raise FeedUnavailable("{} is neither RSS nor Atom".format(feed.outlet))

    items: List[NewsItem] = []
    seen = set()
    undated = 0
    for title, link, summary, published in raw_items:
        title = clean_text(title)
        url = canonical_url(link)
        if not title or not url:
            continue
        key = item_id(url)
        if key in seen:
            continue
        seen.add(key)

        when = _parse_date(published)
        estimated = when is None
        if when is None or when > now + FUTURE_TOLERANCE:
            # Formula1.com's feed carries no pubDate on any item, so every
            # headline from it is undated and fell on the same instant: ten
            # stories sharing one timestamp, ordered arbitrarily among
            # themselves. A feed is published newest first, which is the only
            # ordering evidence there is, so it is kept by spacing undated
            # items a second apart down the feed.
            #
            # Deliberately seconds, not hours. The spacing exists to break a
            # tie, and spreading them over a plausible-looking half a day
            # would be inventing publication times precise enough to be
            # believed. They stay marked estimated, and the page says "first
            # seen" rather than giving a date.
            when, estimated = now - UNDATED_SPACING * undated, True
            undated += 1
        items.append(NewsItem(
            item_id=key,
            source=feed.key,
            outlet=feed.outlet,
            title=title,
            url=url,
            summary=clean_text(summary, MAX_SUMMARY_CHARS),
            published_at=when,
            published_estimated=estimated,
            fetched_at=now,
        ))
    return items


async def fetch_feed(feed: Feed, timeout_seconds: float = 20.0) -> List[NewsItem]:
    """Download and parse one feed. Raises ``FeedUnavailable`` on any failure."""
    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds, follow_redirects=True,
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
            },
        ) as client:
            async with client.stream("GET", feed.url) as response:
                response.raise_for_status()
                chunks: List[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_FEED_BYTES:
                        raise FeedUnavailable(
                            "{} feed exceeded {} bytes".format(feed.outlet, MAX_FEED_BYTES)
                        )
                    chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise FeedUnavailable("could not read the {} feed: {}".format(feed.outlet, exc)) from exc
    return parse_feed(b"".join(chunks), feed)


@dataclass
class RefreshSummary:
    """What one pass over the feeds did, per outlet."""

    finished_at: datetime
    feeds: Dict[str, Dict[str, object]] = field(default_factory=dict)

    @property
    def new(self) -> int:
        return sum(int(f.get("new", 0)) for f in self.feeds.values())


#: The most recent pass, for ``/news/sources``. In memory on purpose: it is an
#: operator's "is this working", and a restart that clears it also starts a
#: fresh pass straight away.
last_refresh: Optional[RefreshSummary] = None


async def refresh(store, feeds: Sequence[Feed] = FEEDS, retention_days: int = 0) -> RefreshSummary:
    """Fetch every feed and store what is new.

    Feeds are independent: one outlet being down costs its own headlines and
    nothing else, and says so in the summary rather than failing the pass.
    """
    global last_refresh
    summary = RefreshSummary(finished_at=datetime.now(timezone.utc))
    for feed in feeds:
        try:
            items = await fetch_feed(feed)
            new = await store.save(items)
            summary.feeds[feed.key] = {"outlet": feed.outlet, "fetched": len(items), "new": new}
        except Exception as exc:
            logger.warning("news feed %s failed: %s", feed.key, exc)
            summary.feeds[feed.key] = {"outlet": feed.outlet, "error": str(exc)}
    if retention_days > 0:
        await store.prune(datetime.now(timezone.utc) - timedelta(days=retention_days))
    summary.finished_at = datetime.now(timezone.utc)
    last_refresh = summary
    return summary
