"""Read API over the regulations corpus.

Its own prefix rather than another entry under ``/data``. Those routes serve
ingested F1 datasets — results, laps, qualifying — filtered by season and round;
"regulations" is not one of them, and the article search takes a question rather
than a key. Routing it through the generic ``/data/{dataset}`` handler meant a
document search arriving as if it were a telemetry query.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_regulation_store
from app.regulations.store import RegulationStore

router = APIRouter(prefix="/regulations", tags=["regulations"])


@router.get("/search", response_model=List[Dict[str, Any]])
async def search(
    q: str = Query(..., min_length=2, description="Question or keywords"),
    season: Optional[int] = Query(None),
    limit: int = Query(5, ge=1, le=25),
    store: RegulationStore = Depends(get_regulation_store),
) -> List[Dict[str, Any]]:
    """Articles matching a question, best first, each with its ``score``.

    Returns the raw hits including the text score. Deciding which of them are
    good enough to act on is the caller's judgement, not this service's:
    core-api is the one that knows whether a passage is about to be handed to a
    language model, and nothing here interprets the corpus.
    """
    return await store.search_regulations(q, season=season, limit=limit)
