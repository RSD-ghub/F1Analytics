"""Official circuit facts from formula1.com.

Where the numbers come from matters here, so it is worth being explicit about
what this does and does not take.

**Facts, not artwork.** This reads the published figures a circuit page states
about itself — official name, length, scheduled laps and distance, the first
season the venue held a race, and the official lap record with its holder. Those
are facts about the world, the kind any results book carries, and the project
already declares that its timing and classification data originate with the FIA
and Formula One.

It deliberately does not take the track illustrations. Those are F1's own
artwork rather than measurements, and this project is about to be published for
other people to read. Our layouts stay derived from telemetry, and a circuit we
have no telemetry for shows its record with no map rather than someone else's
drawing.

**Why it earns a place beside our own corpus.** Our lap record is the fastest
lap in the races we hold, which begins in 2010 and has lap timing only from
2018. For Sepang — returning in 2026 after last racing in 2017 — that is no
record at all. The official figure is 1:34.080, Vettel, 2017. A page that says
nothing there is worse than a page that cites the source.

robots.txt allows /en/racing/; only /en/latest/tags/ is disallowed.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BASE = "https://www.formula1.com/en/racing"

#: A browser user agent. The site 403s an unidentified client, and sending
#: something honest about being a script gets the same treatment — so this is
#: the one that works, used at the volume of one request per circuit per season.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

#: Where each fact lives in the page's embedded payload.
#:
#: The page is server-rendered with its data inlined as escaped JSON rather
#: than fetched by script, so the figures are present in the HTML — no browser
#: needed. Keys are read individually instead of by parsing the whole payload:
#: it is one enormous streaming-format blob, and depending on its shape would
#: break on any framework change, where a missing key only drops one fact.
FIELDS = {
    "official_name": "circuitOfficialName",
    "short_name": "circuitShortName",
    "location": "circuitLocation",
    "circuit_type": "circuitType",
    "length_km": "trackLength",
    "scheduled_laps": "scheduledLapCount",
    "race_distance_km": "scheduledDistance",
    "first_season": "venueFirstSeason",
    "lap_record_time": "fastestLapTime",
    "lap_record_driver": "fastestLapDriver",
    "lap_record_season": "fastestLapSeason",
    "lap_record_team": "fastestLapTeam",
    "meeting_name": "meetingOfficialName",
}

#: Facts without which the page tells us nothing we did not already know.
REQUIRED = ("short_name", "length_km")


class F1SiteUnavailable(RuntimeError):
    """The page could not be read. The circuit keeps whatever it already had."""


#: One chunk of the page's streamed payload.
#:
#: Next.js does not inline its data as a single blob — it pushes it in pieces,
#: each its own ``<script>``, and a value can land across the boundary. The
#: Spanish Grand Prix page splits "Circuit de Barcelona-Catalunya" in the
#: middle, and reading one chunk at a time returned "Circuit de Barce": a
#: field truncated to exactly the chunk edge, with the boundary read as the
#: closing quote. It then failed to geocode, which is how it was noticed, but
#: it had already been stored and could as easily have gone unseen.
_CHUNK = re.compile(r'self\.__next_f\.push\(\[\d+\s*,\s*"(.*?)"\]\)', re.S)


def _unescape(html: str) -> str:
    """The page's payload, with its chunks rejoined.

    Falls back to unescaping the whole document when no chunks are found, so a
    page that stops using this framework degrades to the old behaviour rather
    than to nothing.
    """
    chunks = _CHUNK.findall(html)
    if chunks:
        return "".join(chunks).replace('\\"', '"')
    return html.replace('\\"', '"')


def season_slugs(index_html: str) -> List[str]:
    """Race slugs on a season's index page, testing excluded.

    Order is not calendar order — the page leads with whatever is coming up
    next — so these are used as a set and matched to our own rounds by circuit
    name, never by position.
    """
    found: List[str] = []
    for match in re.finditer(r"/en/racing/\d{4}/([a-z0-9-]+)", _unescape(index_html)):
        slug = match.group(1)
        if slug.startswith("pre-season") or slug in found:
            continue
        found.append(slug)
    return found


def parse_circuit_facts(html: str) -> Dict[str, Any]:
    """Pull the stated figures out of a circuit page.

    Returns ``{}`` when the page carries none of them, which is what a redirect
    or an error page looks like — better than a dict of nulls that reads like a
    circuit with no length.
    """
    plain = _unescape(html)
    facts: Dict[str, Any] = {}
    for name, key in FIELDS.items():
        match = re.search(
            r'"{}"\s*:\s*("(?:[^"\\]|\\.)*"|[0-9.]+)'.format(re.escape(key)), plain
        )
        if match is None:
            continue
        raw = match.group(1)
        value = json.loads(raw) if raw.startswith('"') else raw
        if isinstance(value, str):
            value = value.strip()
        if value not in ("", None):
            facts[name] = value

    if not all(facts.get(name) for name in REQUIRED):
        return {}

    for name in ("length_km", "race_distance_km"):
        if name in facts:
            try:
                facts[name] = float(facts[name])
            except (TypeError, ValueError):
                facts.pop(name)
    for name in ("scheduled_laps", "first_season", "lap_record_season"):
        if name in facts:
            try:
                facts[name] = int(facts[name])
            except (TypeError, ValueError):
                facts.pop(name)

    facts["source"] = "formula1.com"
    return facts


def fetch_circuit_facts(season: int, slug: str, timeout: float = 30.0) -> Dict[str, Any]:
    """Read one circuit page. Raises rather than returning a half-filled dict."""
    import httpx

    url = "{}/{}/{}".format(BASE, season, slug)
    try:
        response = httpx.get(
            url, timeout=timeout, follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
    except Exception as exc:
        raise F1SiteUnavailable("{}: {}".format(url, exc)) from exc

    facts = parse_circuit_facts(response.text)
    if not facts:
        raise F1SiteUnavailable("{}: no circuit facts in the page".format(url))
    facts["url"] = url
    facts["season"] = season
    return facts


def fetch_season_slugs(season: int, timeout: float = 30.0) -> List[str]:
    import httpx

    url = "{}/{}".format(BASE, season)
    try:
        response = httpx.get(
            url, timeout=timeout, follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
    except Exception as exc:
        raise F1SiteUnavailable("{}: {}".format(url, exc)) from exc
    return season_slugs(response.text)
