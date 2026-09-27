"""Venue photographs from Wikimedia Commons, with their licences checked.

The backdrop shows where the next race is being held, and rolls over when that
race is done. That needs photographs of real places, and race and circuit
photography is licensed aggressively — this project is about to be published,
so "found on the internet" is not a provenance.

Commons is the right source precisely because its licensing is machine
readable. Every file carries ``extmetadata`` naming its licence and its
author, so a permissive licence can be *verified* rather than assumed, and the
attribution the licence requires can be rendered from the same record. Nothing
is stored here without both.

What this deliberately will not do is keep a file whose licence it does not
recognise. An unknown licence is not a permissive one, and the cost of being
wrong is a takedown on someone else's photograph.
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

API = "https://commons.wikimedia.org/w/api.php"

#: Commons asks every client to identify itself and is entitled to refuse one
#: that does not.
USER_AGENT = "F1Forecast/0.1 (hobby project; +https://github.com/f1-forecast)"

#: Licences permitting reuse on a public site, given attribution.
#:
#: Matched as prefixes against ``LicenseShortName``, which is a short human
#: string like "CC BY-SA 4.0" or "Public domain". Deliberately a whitelist:
#: Commons also hosts non-free logos and fair-use material, and a blacklist
#: would admit anything nobody thought to exclude.
PERMISSIVE = ("cc0", "cc by", "public domain", "pd-", "attribution")

#: Checked before the whitelist, because the whitelist alone is not enough.
#:
#: "CC BY-NC 4.0" starts with "cc by" and is not a licence this may use: NC is
#: non-commercial, and a public site is not obviously non-commercial. ND
#: forbids derivative works, and this page crops a photograph to the viewport
#: and washes it back — near enough to an adaptation that it is not worth
#: arguing. Both were accepted by the first version of this filter, and a test
#: caught it rather than a rights holder.
REFUSED = ("-nc", "nc-", " nc ", "-nd", "nd-", " nd ", "noncommercial",
           "no derivative")

#: Files that are not photographs of a place. Commons returns a circuit's logo
#: and its track diagram for the same search as its grandstands, and neither
#: makes a backdrop.
NOT_A_VENUE = (
    "logo", "map", "diagram", "layout", "circuit map", "track map",
    "svg", "icon", "flag", "poster", "ticket", "chart",
)

#: Smallest usable image. A backdrop spans the viewport, and a thumbnail
#: stretched across it looks like a mistake.
MIN_WIDTH = 1200


class CommonsUnavailable(RuntimeError):
    """No usable imagery. The backdrop falls back to circuit outlines."""


def _text(html: Optional[str]) -> str:
    """The author's name out of the markup Commons stores it in.

    ``Artist`` is an HTML fragment — usually a link to a user page — because
    that is how the wiki records it. Rendering it raw would put someone else's
    markup into our page.
    """
    if not html:
        return ""
    plain = re.sub(r"<[^>]+>", "", str(html))
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain


def is_permissive(licence: str) -> bool:
    lowered = (licence or "").strip().lower()
    if any(token in lowered for token in REFUSED):
        return False
    return any(lowered.startswith(p) for p in PERMISSIVE)


def looks_like_a_venue(title: str) -> bool:
    lowered = (title or "").lower()
    return not any(token in lowered for token in NOT_A_VENUE)


def usable_images(pages: Dict[str, Any], limit: int = 4) -> List[Dict[str, Any]]:
    """Photographs we may publish, with the credit their licence requires.

    Pure so the filtering can be tested without the network — which matters
    more here than usual, because the thing being tested is whether we are
    entitled to use somebody's photograph.
    """
    kept: List[Dict[str, Any]] = []
    for page in (pages or {}).values():
        title = page.get("title", "")
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        licence = (meta.get("LicenseShortName") or {}).get("value", "")

        if not is_permissive(licence):
            logger.debug("skipping %s: licence %r", title, licence)
            continue
        if not looks_like_a_venue(title):
            continue
        url = info.get("thumburl") or info.get("url")
        if not url or (info.get("thumbwidth") or 0) < MIN_WIDTH:
            continue

        author = _text((meta.get("Artist") or {}).get("value"))
        kept.append({
            "url": url,
            "title": title.replace("File:", "").rsplit(".", 1)[0],
            "credit": author or "Wikimedia Commons",
            "licence": licence,
            "licence_url": (meta.get("LicenseUrl") or {}).get("value", ""),
            "source": page.get("descriptionurl") or info.get("descriptionurl", ""),
        })
        if len(kept) >= limit:
            break
    return kept


def search_venue(query: str, limit: int = 4, timeout: float = 30.0):
    """Photographs of a named place, already filtered to what we may use."""
    import httpx

    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": query, "gsrnamespace": 6, "gsrlimit": 12,
        "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 1920,
    }
    try:
        response = httpx.get(
            API, params=params, timeout=timeout,
            headers={"User-Agent": USER_AGENT}, follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise CommonsUnavailable("{}: {}".format(query, exc)) from exc

    images = usable_images((payload.get("query") or {}).get("pages") or {}, limit)
    if not images:
        raise CommonsUnavailable(
            "no freely licensed photograph of {} on Commons".format(query)
        )
    return images
