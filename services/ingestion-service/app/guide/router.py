"""Read API over the strategy guide.

Its own prefix, like the regulations and the news: a corpus searched by
question, not an ingested F1 dataset filtered by season and round. Read-only
on purpose — the book is loaded from a file by ``scripts/ingest_strategy_guide.py``,
so there is no route that accepts a book's text over HTTP.
"""

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_guide_store
from app.guide.store import GuideStore

router = APIRouter(prefix="/guide", tags=["guide"])


@router.get("/search", response_model=List[Dict[str, Any]])
async def search(
    q: str = Query(..., min_length=2, description="Question or keywords"),
    limit: int = Query(5, ge=1, le=20),
    chapter: List[int] = Query(default=[], description="Restrict to these chapters"),
    store: GuideStore = Depends(get_guide_store),
) -> List[Dict[str, Any]]:
    """Passages matching a question, best first, each with its ``score``.

    Raw hits, score included: whether a passage is good enough to put in front
    of a reader or a language model is core-api's judgement, as it is for the
    regulations.
    """
    return await store.search(q, limit=limit, chapters=chapter)


@router.get("/status", response_model=Dict[str, Any])
async def status(store: GuideStore = Depends(get_guide_store)) -> Dict[str, Any]:
    """Which books the guide holds, so an empty one is visible rather than silent."""
    return await store.status()
