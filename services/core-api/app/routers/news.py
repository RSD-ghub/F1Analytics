"""Paddock news — the outlets' race-week headlines, passed through.

Public, like One Blog: headlines link out to the outlets that published them
and spend no model calls, so there is nothing to meter and nothing private.

core-api adds nothing to the items but a label. It does not rewrite, score or
summarise them, because anything it said about a story would be a claim of
ours stacked on a report we have not checked. The one thing it does do is
reorder them, so that one outlet's run of stories cannot fill the whole page —
a presentation rule that drops nothing and leaves every timestamp alone. See
``app.services.news``.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.services.downstream import ServiceClient
from app.services.news import lead_with_picture, spread_outlets

router = APIRouter(prefix="/news", tags=["news"])

#: Said once in the payload, not left to the page, for the reason Bernie's
#: disclaimer is: a client that forgets to render it would be presenting
#: someone else's report as ours.
ATTRIBUTION = (
    "Headlines are the outlets' own reports, linked to the originals. We have "
    "not checked them, and the forecast does not use them."
)


#: Ask the store for this many times the headlines we will show, so that the
#: trim takes an already mixed list rather than the undated stories that an
#: outlet which dates nothing puts at the top of every window.
POOL = 3

#: What the ingestion route will give us at most.
MAX_POOL = 50


class NewsItem(BaseModel):
    id: str
    source: str
    outlet: str
    title: str
    url: str
    summary: str = ""
    published_at: Optional[str] = None
    #: The feed gave no date; ``published_at`` is when we first saw the item.
    published_estimated: bool = False
    #: The feed's own picture for the item; "" when the outlet sends none.
    image_url: str = ""


class NewsPage(BaseModel):
    items: List[NewsItem] = Field(default_factory=list)
    attribution: str = ATTRIBUTION
    #: Present only for a race week, so the page can say which week it is.
    since: Optional[str] = None
    until: Optional[str] = None


def _client(settings: Settings) -> ServiceClient:
    return ServiceClient(
        "ingestion", settings.ingestion_service_url,
        settings.downstream_timeout_seconds,
    )


def _items(rows: List[Dict[str, Any]]) -> List[NewsItem]:
    return [
        NewsItem(**{key: row[key] for key in NewsItem.model_fields
                    if row.get(key) is not None})
        for row in rows
        if row.get("id") and row.get("title") and row.get("url")
    ]


@router.get("", response_model=NewsPage)
async def latest(
    limit: int = Query(30, ge=1, le=50),
    source: Optional[str] = Query(None, max_length=32),
    settings: Settings = Depends(get_settings),
) -> NewsPage:
    """The newest headlines across every outlet, or one."""
    params: Dict[str, Any] = {"limit": limit}
    if source:
        params["source"] = source
    rows = await _client(settings).get("/news", params)
    return NewsPage(items=_items(lead_with_picture(rows or [])))


@router.get("/weekend/{season}/{round_number}", response_model=NewsPage)
async def weekend(
    season: int,
    round_number: int,
    limit: int = Query(12, ge=1, le=50),
    settings: Settings = Depends(get_settings),
) -> NewsPage:
    """One race week's headlines, with the window they were taken from.

    Asks for more than it returns, because trimming before mixing is the same
    as not mixing — see ``POOL``.
    """
    payload = await _client(settings).get(
        "/news/weekend/{}/{}".format(season, round_number),
        {"limit": min(limit * POOL, MAX_POOL)},
    ) or {}
    return NewsPage(
        items=_items(spread_outlets(payload.get("items") or []))[:limit],
        since=payload.get("since"),
        until=payload.get("until"),
    )
