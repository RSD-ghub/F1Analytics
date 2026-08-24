"""Bernie — explanation and conversation endpoints.

Every response carries the disclaimer in the payload rather than leaving it to
the frontend. A caller that forgets to render it would be presenting an AI
persona as a person, and that must not depend on client-side discipline.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.dependencies import current_user, get_conversations, get_llm
from app.models.conversation import (
    AskRequest,
    BernieTurnResponse,
    Role,
    StartThreadRequest,
    Thread,
    ThreadSummary,
)
from app.services.bernie import (
    DISCLAIMER,
    Bernie,
    BernieUnavailable,
    why_this_prediction_facts,
)
from app.services.conversation import (
    ConversationStore,
    ThreadNotFound,
    clamp_question,
    history_for_prompt,
)
from app.services.downstream import ServiceClient, gather_optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bernie", tags=["bernie"])


class BernieResponse(BaseModel):
    answer: str
    disclaimer: str = DISCLAIMER
    #: The facts the answer was allowed to draw on. Returned so a reader can
    #: check the prose against its inputs — the same auditability the
    #: predictions have.
    grounded_on: Dict[str, Any] = Field(default_factory=dict)





@router.get("/why/{season}/{round_number}", response_model=BernieResponse)
async def why_this_prediction(
    season: int,
    round_number: int,
    settings: Settings = Depends(get_settings),
    llm=Depends(get_llm),
) -> BernieResponse:
    """Explain a locked forecast in strategist's terms.

    Public: an unexplained probability is exactly the thing this product exists
    not to publish.
    """
    prediction_client = ServiceClient(
        "prediction", settings.prediction_service_url,
        settings.downstream_timeout_seconds,
    )
    predictions = await prediction_client.get(
        "/predictions/{}/{}".format(season, round_number)
    )
    if not predictions:
        raise HTTPException(
            status_code=404,
            detail="no locked forecast for {}-{}".format(season, round_number),
        )

    # Prefer the grid-aware call: it is the more confident of the two and the
    # one a reader is most likely asking about.
    prediction = next(
        (p for p in predictions if p.get("window") == "post_quali"), predictions[0]
    )
    snapshot = None
    if prediction.get("feature_snapshot_ref"):
        try:
            snapshot = await prediction_client.get(
                "/predictions/snapshot/{}".format(prediction["feature_snapshot_ref"])
            )
        except Exception:
            logger.info("snapshot unavailable; explaining without as-of context")

    facts = why_this_prediction_facts(prediction, snapshot)
    bernie = Bernie(llm)
    try:
        answer = await bernie.explain(
            "Why does the model see the race this way?", facts
        )
    except BernieUnavailable as exc:
        # The facts are the substance; the prose is the polish. Returning them
        # unnarrated beats a 503 on a page whose job is to explain.
        logger.info("Bernie unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Bernie is not configured on this deployment. The forecast and "
                "its inputs are available from /predictions."
            ),
        )
    return BernieResponse(answer=answer, grounded_on=facts)


# ── Conversation ─────────────────────────────────────────────────────────────
#
# Threads are authenticated. This is the only surface that spends an LLM call on
# arbitrary user input, so it is the only one that needs an account attached to
# the cost — and conversations are private to their owner.


async def _weekend_facts(
    settings: Settings, season: int, round_number: int
) -> Dict[str, Any]:
    """Re-derive the facts for a weekend. Called on **every** turn.

    Not cached onto the thread on purpose. Facts change during a weekend —
    qualifying happens, a forecast locks, penalties land — and a conversation
    that answered from a snapshot taken at turn one would go stale mid-thread
    while sounding equally confident.
    """
    prediction_client = ServiceClient(
        "prediction", settings.prediction_service_url,
        settings.downstream_timeout_seconds,
    )
    ingestion_client = ServiceClient(
        "ingestion", settings.ingestion_service_url,
        settings.downstream_timeout_seconds,
    )

    fetched = await gather_optional(
        predictions=prediction_client.get(
            "/predictions/{}/{}".format(season, round_number)
        ),
        practice=ingestion_client.get(
            "/data/practice",
            {"season": season, "round": round_number, "limit": 60},
        ),
    )

    facts: Dict[str, Any] = {"race": "{} round {}".format(season, round_number)}

    predictions = fetched.get("predictions") or []
    if predictions:
        preferred = next(
            (p for p in predictions if p.get("window") == "post_quali"), predictions[0]
        )
        facts.update(why_this_prediction_facts(preferred, None))
    else:
        facts["forecast status"] = (
            "No forecast is locked for this race yet, so there are no "
            "probabilities to discuss."
        )

    practice = [
        row for row in (fetched.get("practice") or [])
        if (row.get("long_run_seconds") or 0) > 0
    ]
    if practice:
        ranked = sorted(practice, key=lambda r: r["long_run_seconds"])[:5]
        reference = ranked[0]["long_run_seconds"]
        facts["practice long-run pace"] = [
            "{} {:+.2f}s a lap over {} laps ({})".format(
                row["driver"],
                row["long_run_seconds"] - reference,
                row.get("long_run_laps", 0),
                row.get("session_name", "practice"),
            )
            for row in ranked
        ]

    if fetched["_unavailable"]:
        facts["unavailable"] = (
            "Some data could not be loaded: {}. Say so if asked about it.".format(
                ", ".join(fetched["_unavailable"])
            )
        )
    return facts


@router.post("/threads", response_model=BernieTurnResponse, status_code=201)
async def start_thread(
    request: StartThreadRequest,
    settings: Settings = Depends(get_settings),
    llm=Depends(get_llm),
    store: ConversationStore = Depends(get_conversations),
    user=Depends(current_user),
) -> BernieTurnResponse:
    """Open a conversation about a race weekend."""
    bernie = Bernie(llm)
    if not bernie.available:
        raise HTTPException(
            status_code=503,
            detail="Bernie is not configured on this deployment.",
        )

    facts = await _weekend_facts(settings, request.season, request.round)
    thread = await store.create(user["_id"], request.season, request.round)
    return await _turn(bernie, store, thread, request.question, facts)


@router.post("/threads/{thread_id}", response_model=BernieTurnResponse)
async def continue_thread(
    thread_id: str,
    request: AskRequest,
    settings: Settings = Depends(get_settings),
    llm=Depends(get_llm),
    store: ConversationStore = Depends(get_conversations),
    user=Depends(current_user),
) -> BernieTurnResponse:
    """Ask a follow-up. History gives continuity; facts are re-derived fresh."""
    bernie = Bernie(llm)
    if not bernie.available:
        raise HTTPException(status_code=503, detail="Bernie is not configured.")

    try:
        thread = await store.get(thread_id, user["_id"])
    except ThreadNotFound as exc:
        # 404 rather than 403 for someone else's thread: confirming that an id
        # exists but belongs to another account is itself a disclosure.
        raise HTTPException(status_code=404, detail=str(exc))

    facts = await _weekend_facts(settings, thread.season, thread.round)
    return await _turn(bernie, store, thread, request.question, facts)


@router.get("/threads", response_model=List[ThreadSummary])
async def list_threads(
    store: ConversationStore = Depends(get_conversations),
    user=Depends(current_user),
) -> List[ThreadSummary]:
    return await store.list_for(user["_id"])


@router.get("/threads/{thread_id}", response_model=Thread)
async def read_thread(
    thread_id: str,
    store: ConversationStore = Depends(get_conversations),
    user=Depends(current_user),
) -> Thread:
    try:
        thread = await store.get(thread_id, user["_id"])
    except ThreadNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    # Reasoning traces never leave the server.
    for turn in thread.turns:
        turn.reasoning = None
    return thread


async def _turn(
    bernie: Bernie,
    store: ConversationStore,
    thread: Thread,
    question: str,
    facts: Dict[str, Any],
) -> BernieTurnResponse:
    """One exchange: record the question, answer it, record the answer.

    The question is stored before the model is called so a failed or timed-out
    turn still leaves the thread coherent — otherwise a user sees their own
    message vanish on an error.
    """
    question = clamp_question(question)
    if not question:
        raise HTTPException(status_code=422, detail="question is empty")

    history = history_for_prompt(thread)
    await store.append(thread, Role.USER, question)

    try:
        answer, reasoning = await bernie.converse(question, facts, history=history)
    except BernieUnavailable as exc:
        # 402 upstream stays a 503 here: the caller did nothing wrong and has no
        # payment relationship with us. The reason is surfaced so an operator
        # can act on it without reading logs.
        raise HTTPException(
            status_code=503,
            detail={"reason": getattr(exc, "reason", "unavailable"), "message": str(exc)},
        )

    await store.append(thread, Role.ASSISTANT, answer, reasoning=reasoning)
    return BernieTurnResponse(
        thread_id=thread.thread_id,
        answer=answer,
        disclaimer=DISCLAIMER,
        grounded_on=facts,
        turn_count=len(thread.turns),
    )
