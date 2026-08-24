"""Read APIs over the ingested dataset.

Consumed by core-api (for the analytics drill-down layer) and prediction-service
(for feature building). Nothing here interprets the data — that is deliberately
the callers' job, so this service stays a data boundary rather than growing a
second copy of the domain logic.

Every endpoint is bounded by an explicit ``limit``: a single unpaginated laps
query spans millions of rows.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo import ASCENDING, DESCENDING

from app.dependencies import get_store
from app.services.storage import DATA_COLLECTIONS, READABLE_COLLECTIONS, IngestionStore

router = APIRouter(prefix="/data", tags=["data"])

#: Hard ceiling regardless of what a caller asks for. Prediction-service reads
#: bulk history from the Parquet dataset, not from here.
MAX_LIMIT = 20000


def _collection_or_404(name: str) -> str:
    if name not in READABLE_COLLECTIONS:
        raise HTTPException(
            status_code=404,
            detail="unknown dataset '{}'; available: {}".format(
                name, ", ".join(sorted(READABLE_COLLECTIONS))
            ),
        )
    return READABLE_COLLECTIONS[name]


def _session_query(
    season: Optional[int], round_number: Optional[int], driver: Optional[str]
) -> Dict[str, Any]:
    query: Dict[str, Any] = {}
    if season is not None:
        query["season"] = season
    if round_number is not None:
        query["round"] = round_number
    if driver:
        query["driver"] = driver
    return query


@router.get("/seasons", response_model=List[int])
async def seasons(store: IngestionStore = Depends(get_store)) -> List[int]:
    """Seasons with ingested results — what the UI can actually offer."""
    return await store.distinct_seasons()


@router.get("/datasets", response_model=Dict[str, int])
async def datasets(store: IngestionStore = Depends(get_store)) -> Dict[str, int]:
    """Row counts per dataset. Cheap sanity check that ingestion did something."""
    return {
        name: await store.count_rows(collection)
        for name, collection in READABLE_COLLECTIONS.items()
    }


@router.get("/results", response_model=List[Dict[str, Any]])
async def results(
    season: Optional[int] = Query(None),
    round_number: Optional[int] = Query(None, alias="round"),
    driver: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=MAX_LIMIT),
    store: IngestionStore = Depends(get_store),
) -> List[Dict[str, Any]]:
    return await store.find_rows(
        DATA_COLLECTIONS["results"],
        _session_query(season, round_number, driver),
        limit=limit,
        sort=[("season", DESCENDING), ("round", DESCENDING), ("position", ASCENDING)],
    )


@router.get("/{dataset}", response_model=List[Dict[str, Any]])
async def dataset_rows(
    dataset: str,
    season: Optional[int] = Query(None),
    round_number: Optional[int] = Query(None, alias="round"),
    driver: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=MAX_LIMIT),
    store: IngestionStore = Depends(get_store),
) -> List[Dict[str, Any]]:
    """Generic access to any ingested dataset.

    Kept generic rather than one endpoint per dataset: these are raw rows with no
    per-dataset semantics to encode, and seven near-identical handlers would
    drift apart the moment one of them grew a filter.
    """
    collection = _collection_or_404(dataset)
    return await store.find_rows(
        collection,
        _session_query(season, round_number, driver),
        limit=limit,
        sort=[("season", DESCENDING), ("round", DESCENDING)],
    )
