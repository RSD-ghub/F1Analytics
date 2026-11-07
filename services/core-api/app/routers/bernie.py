"""Bernie — explanation and conversation endpoints.

Every response carries the disclaimer in the payload rather than leaving it to
the frontend. A caller that forgets to render it would be presenting an AI
persona as a person, and that must not depend on client-side discipline.
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from f1_common.llm import _split_leaked_reasoning as split_leaked_reasoning
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.dependencies import (
    current_user,
    get_conversations,
    get_llm,
    get_usage,
)
from app.services.usage import BudgetExceeded, UsageStore
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
    grid_penalty_facts,
    guide_facts,
    news_facts,
    regulation_facts,
    why_this_prediction_facts,
)
from app.services.conversation import (
    ConversationStore,
    ThreadNotFound,
    clamp_question,
    history_for_prompt,
)
from app.services import weather
from app.services.downstream import ServiceClient, gather_optional
from app.routers.strategy import as_mapping, as_rows, load_race
from app.services.strategy import build_inputs, simulate, strategy_facts
from app.services.strategy_engine import (
    build_field,
    engine_facts,
    mentioned_driver,
    plan_race,
)

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
    usage: UsageStore = Depends(get_usage),
    user=Depends(current_user),
) -> BernieResponse:
    """Explain a locked forecast in strategist's terms.

    The explanation is cached against the prediction rather than regenerated per
    reader. A locked forecast is immutable, so the explanation of it is a fixed
    fact about a fixed thing — generating it once makes the cost of explanations
    scale with the number of forecasts rather than the number of visitors.

    That matters for more than the bill: this route exists because an
    unexplained probability is the thing the product is trying not to publish,
    and a per-reader cost is what would eventually force it behind a meter.
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
        (p for p in predictions if p.get("window") in ("final_grid", "post_quali")),
        predictions[0],
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

    # Keyed on the prediction, not the race: the two windows are different
    # forecasts and deserve different explanations. Keyed on the model version
    # too, so a retrain does not serve stale reasoning about superseded numbers.
    cache_key = usage.key(
        "why", prediction.get("prediction_id"), prediction.get("model_version")
    )
    cached = await usage.cached(cache_key)
    if cached:
        return BernieResponse(**cached)

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
    response = BernieResponse(answer=answer, grounded_on=facts)
    await usage.store(cache_key, response.model_dump(mode="json"))
    return response


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
        # The starting order. Its absence was not a gap in the data — we hold
        # it — it was a gap in what Bernie was told. Asked who qualified on the
        # second row at Spain he answered that the facts list probabilities and
        # not grid slots, which was true of the facts and false of the archive.
        qualifying=ingestion_client.get(
            "/data/qualifying",
            {"season": season, "round": round_number, "limit": 40},
        ),
        # For the race-day weather. Only the next weekend can be inside the
        # forecast horizon, so that is the only one asked about.
        next_weekend=ingestion_client.get("/forward/next"),
        circuit=ingestion_client.get("/circuits/{}/{}".format(season, round_number)),
    )

    facts: Dict[str, Any] = {"race": "{} round {}".format(season, round_number)}

    predictions = fetched.get("predictions") or []
    if predictions:
        # Most-informed window, not "post_quali" by name — otherwise the
        # confirmed-grid forecast is silently ignored the moment it exists.
        preferred = next(
            (p for p in predictions if p.get("window") == "final_grid"),
            next((p for p in predictions if p.get("window") == "post_quali"),
                 predictions[0]),
        )
        facts.update(why_this_prediction_facts(preferred, None))
    else:
        facts["forecast status"] = (
            "No forecast is locked for this race yet, so there are no "
            "probabilities to discuss."
        )

    rows = [row for row in (fetched.get("qualifying") or []) if row.get("driver")]
    if rows:
        # Both orders, always — they answer different questions and each row
        # carries both values. Emitting only one of them meant that once the
        # grid was confirmed, Bernie could describe the race but had genuinely
        # lost qualifying: asked who was on the second row he refused, and was
        # right to, because the classification had been dropped from his facts.
        classified = sorted(
            (r for r in rows if (r.get("position") or 999) < 999),
            key=lambda r: r["position"],
        )
        if classified:
            facts["qualifying classification"] = [
                "Q{} {} ({})".format(r["position"], r["driver"], r.get("team", "?"))
                for r in classified
            ]
            no_time = [r["driver"] for r in rows if (r.get("position") or 999) >= 999]
            if no_time:
                facts["set no qualifying time"] = no_time

        started = sorted(
            (r for r in rows if (r.get("grid_position") or 0) > 0),
            key=lambda r: r["grid_position"],
        )
        if started:
            facts["confirmed starting order"] = [
                "P{} {} ({}){}".format(
                    r["grid_position"], r["driver"], r.get("team", "?"),
                    "  [pit lane]" if r.get("starts_from_pit_lane") else "",
                )
                for r in started
            ]
            moved = [
                "{}: qualified Q{}, starts P{}".format(
                    r["driver"], r["position"], r["grid_position"]
                )
                for r in started
                if (r.get("position") or 999) < 999
                and r["position"] != r["grid_position"]
            ]
            if moved:
                facts["moved between qualifying and the grid"] = moved

        # Built from every row, not just ``started``: a driver who set no
        # qualifying time still carries a penalty, and Stroll took forty places
        # at Spain having never set one.
        facts.update(grid_penalty_facts(rows))

    practice = [
        row for row in (fetched.get("practice") or [])
        if (row.get("long_run_seconds") or 0) > 0
    ]
    if practice:
        # The whole field, not the top five: a strategy question is as often
        # about the car in eighth, and its race pace against the cars around
        # it is what decides whether an offset strategy can work. One line per
        # driver, their quickest long run, so three sessions do not triple it.
        quickest: Dict[str, Dict[str, Any]] = {}
        for row in practice:
            held = quickest.get(row["driver"])
            if held is None or row["long_run_seconds"] < held["long_run_seconds"]:
                quickest[row["driver"]] = row
        ranked = sorted(quickest.values(), key=lambda r: r["long_run_seconds"])
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

    upcoming = fetched.get("next_weekend") or {}
    if (upcoming.get("season"), upcoming.get("round")) == (season, round_number):
        facts.update(weather.weather_facts(await weather.race_forecast(
            fetched.get("circuit"), upcoming.get("race_start_utc"),
            url=settings.weather_forecast_url,
            timeout=settings.weather_timeout_seconds,
        )))

    if fetched["_unavailable"]:
        facts["unavailable"] = (
            "Some data could not be loaded: {}. Say so if asked about it.".format(
                ", ".join(fetched["_unavailable"])
            )
        )
    return facts



async def _strategy_facts(
    settings: Settings, season: int, round_number: int, question: str = ""
) -> Dict[str, Any]:
    """Simulate the race's strategy options and plan it, on every turn.

    Always included, never gated on the question: "what should Norris do from
    P8?" names no tyre, and a gate guessing which questions are about strategy
    would be wrong in exactly the way the regulations search avoids by running
    unconditionally.

    Never empty either. With no history for a circuit — or ingestion down — the
    simulation runs on generic figures and says so line by line. Telling a
    reader there is no strategy because a measurement is missing is the
    dismissal this exists to replace; a strategist at a new circuit still has
    a plan.

    The engine plans for the car the question names, when the grid holds one,
    so "what should Norris do from P8?" gets Norris's rejoin and undercut, not
    the leader's. It is the plan the Strategy page shows for the same car.
    """
    fetched = await load_race(settings, season, round_number)
    circuit = as_mapping(fetched.get("circuit"))
    official = circuit.get("official") or {}
    profile = fetched.get("profile") if isinstance(fetched.get("profile"), dict) else None
    field = build_field(as_rows(fetched.get("qualifying")), as_rows(fetched.get("practice")))
    car = mentioned_driver(field, clamp_question(question))
    stats = circuit.get("stats") or {}

    def run():
        sim = simulate(build_inputs(profile, official.get("scheduled_laps")))
        return sim, plan_race(sim, stats, field, car)

    # Pure arithmetic, but a few hundred thousand steps of it: off the event
    # loop so one Bernie turn does not stall every other request.
    sim, plan = await asyncio.to_thread(run)
    facts = strategy_facts(sim)
    facts.update(engine_facts(plan))

    # How much track position is worth here, which the simulation leaves out:
    # an extra stop that is quicker on paper is no use at a circuit where the
    # order does not change.
    position: List[str] = []
    if stats.get("mean_places_changed") is not None:
        position.append(
            "classified finishers here gained or lost {} places on average between "
            "grid and flag (over {} finishes)".format(
                stats["mean_places_changed"], stats.get("places_changed_sample", "?"))
        )
    if stats.get("pole_win_rate") is not None:
        position.append("pole won {:.0%} of {} races here".format(
            stats["pole_win_rate"], stats.get("pole_starts", "?")))
    elif stats.get("pole_starts"):
        position.append("pole won {} of {} races here".format(
            stats.get("pole_wins", 0), stats["pole_starts"]))
    if position:
        facts["track position at this circuit"] = position

    if {"profile", "circuit"} & set(fetched["_unavailable"]):
        facts["note on the strategy simulation"] = (
            "This circuit's history could not be loaded, so the simulation used "
            "generic figures wherever its inputs say GENERIC."
        )
    return facts


async def _guide_facts(settings: Settings, question: str) -> Dict[str, Any]:
    """Passages from the strategy guide for **this** question, on every turn.

    Ungated, like the regulations, for the same reason: "should they gamble on
    a safety car from the back?" is a strategy-philosophy question that names
    nothing a keyword list would catch. Bernie is told to pass over passages
    that do not bear on the question.
    """
    question = clamp_question(question)
    if len(question) < 2:
        return {}
    ingestion_client = ServiceClient(
        "ingestion", settings.ingestion_service_url,
        settings.downstream_timeout_seconds,
    )
    try:
        hits = await ingestion_client.get("/guide/search", {"q": question, "limit": 5})
    except Exception as exc:
        logger.warning("strategy guide unavailable: %s", exc)
        return {
            "note on the strategy guide": (
                "The strategy guide could not be searched for this question. Plan "
                "from the strategy engine and say so if asked what the guide says."
            )
        }
    return guide_facts(hits if isinstance(hits, list) else [])


async def _regulation_facts(
    settings: Settings, question: str, season: int
) -> Dict[str, Any]:
    """Look up rules text for **this** question, on every turn.

    Retrieval is driven by the question rather than the weekend, which is why it
    lives here instead of in ``_weekend_facts``: "who is quickest in practice"
    and "how many power unit elements before a penalty" want the same weekend
    and completely different passages.

    Searched unconditionally rather than behind a keyword gate. Any gate would
    have to guess which questions are about the rules, and "how many engines can
    they use" contains none of the words one would think to list. Cheaper to let
    the search run and let Bernie pass over what does not bear on the question —
    which the system prompt tells him to do — than to decide in advance that a
    question is not a regulations question and be wrong.
    """
    question = clamp_question(question)
    if not question:
        return {}

    ingestion_client = ServiceClient(
        "ingestion", settings.ingestion_service_url,
        settings.downstream_timeout_seconds,
    )
    try:
        # Scoped to the race's own season. The FIA reissues sections constantly
        # and a 2026 rule is not evidence about a 2024 race, so a season we hold
        # no corpus for correctly yields nothing rather than the wrong rulebook.
        hits = await ingestion_client.get(
            "/regulations/search",
            {"q": question, "season": season, "limit": 6},
        )
    except Exception as exc:
        # Enrichment, not substance: a turn about practice pace does not need
        # the rulebook. Stated rather than swallowed, because the alternative is
        # Bernie saying he has no rule on a subject the corpus covers, with
        # nothing on the page to say why.
        logger.warning("regulation search unavailable: %s", exc)
        return {
            "note on the regulations": (
                "The FIA regulations could not be searched for this question, "
                "so no rule text is available here. Say so if asked about the "
                "rules rather than answering from memory."
            )
        }

    # Over-fetch six, render at most three. The relative score floor decides
    # how many of the six are in the same class as the best hit, so asking for
    # exactly three would throw away the evidence that decision needs.
    return regulation_facts(hits or [])


async def _news_facts(
    settings: Settings, question: str, season: int, round_number: int
) -> Dict[str, Any]:
    """Race-week headlines for **this** question, on every turn.

    Two calls: the week's newest headlines, and the week's headlines matching
    the question. Per turn rather than per thread for the reason the weekend
    facts are — news lands during a weekend, and a thread started on Thursday
    should know on Saturday what was reported on Friday.
    """
    ingestion_client = ServiceClient(
        "ingestion", settings.ingestion_service_url,
        settings.downstream_timeout_seconds,
    )
    question = clamp_question(question)
    calls = {
        "latest": ingestion_client.get(
            "/news/weekend/{}/{}".format(season, round_number), {"limit": 6}
        ),
    }
    if len(question) >= 2:
        calls["matching"] = ingestion_client.get(
            "/news/search",
            {"q": question, "season": season, "round": round_number, "limit": 4},
        )
    fetched = await gather_optional(**calls)

    latest = (fetched.get("latest") or {}).get("items") or []
    facts = news_facts(fetched.get("matching") or [], latest)
    if fetched["_unavailable"] and not facts:
        # Stated rather than swallowed, as with the regulations: without it a
        # question about the paddock gets "I have no news" with nothing to say
        # the news was simply unreachable. A round with no known start date
        # lands here too, and the note is still true of it.
        facts["note on paddock news"] = (
            "Race-week news could not be loaded for this question. Say so if "
            "asked about the news rather than answering from memory."
        )
    return facts


async def _charge(usage: UsageStore, settings: Settings, user) -> None:
    """Spend one unit of this caller's daily allowance, or 429.

    Charged before the call, not after: a prompt that fails upstream has still
    cost us the attempt, and billing only successes would let someone retry a
    failing question without limit.
    """
    try:
        await usage.consume(
            str(user["_id"]),
            per_user=settings.bernie_calls_per_user_per_day,
            per_day=settings.bernie_calls_per_day,
        )
    except BudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc))


@router.post("/threads", response_model=BernieTurnResponse, status_code=201)
async def start_thread(
    request: StartThreadRequest,
    settings: Settings = Depends(get_settings),
    llm=Depends(get_llm),
    store: ConversationStore = Depends(get_conversations),
    usage: UsageStore = Depends(get_usage),
    user=Depends(current_user),
    stream: bool = Query(False, description="Stream the answer as server-sent events"),
) -> BernieTurnResponse:
    """Open a conversation about a race weekend.

    With ``?stream=true`` the answer arrives as server-sent events; see
    ``_turn_stream`` for the event shapes.
    """
    bernie = Bernie(llm)
    if not bernie.available:
        raise HTTPException(
            status_code=503,
            detail="Bernie is not configured on this deployment.",
        )

    await _charge(usage, settings, user)
    facts = await _weekend_facts(settings, request.season, request.round)
    facts.update(await _regulation_facts(settings, request.question, request.season))
    facts.update(await _news_facts(
        settings, request.question, request.season, request.round
    ))
    facts.update(await _guide_facts(settings, request.question))
    facts.update(await _strategy_facts(
        settings, request.season, request.round, request.question
    ))
    thread = await store.create(user["_id"], request.season, request.round)
    if stream:
        return _turn_stream(bernie, store, thread, request.question, facts)
    return await _turn(bernie, store, thread, request.question, facts)


@router.post("/threads/{thread_id}", response_model=BernieTurnResponse)
async def continue_thread(
    thread_id: str,
    request: AskRequest,
    settings: Settings = Depends(get_settings),
    llm=Depends(get_llm),
    store: ConversationStore = Depends(get_conversations),
    usage: UsageStore = Depends(get_usage),
    user=Depends(current_user),
    stream: bool = Query(False, description="Stream the answer as server-sent events"),
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

    await _charge(usage, settings, user)
    facts = await _weekend_facts(settings, thread.season, thread.round)
    facts.update(await _regulation_facts(settings, request.question, thread.season))
    facts.update(await _news_facts(
        settings, request.question, thread.season, thread.round
    ))
    facts.update(await _guide_facts(settings, request.question))
    facts.update(await _strategy_facts(
        settings, thread.season, thread.round, request.question
    ))
    if stream:
        return _turn_stream(bernie, store, thread, request.question, facts)
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


def _sse(event: str, data: Dict[str, Any]) -> str:
    return "event: {}\ndata: {}\n\n".format(event, json.dumps(data, default=str))


def _turn_stream(
    bernie: Bernie,
    store: ConversationStore,
    thread: Thread,
    question: str,
    facts: Dict[str, Any],
) -> StreamingResponse:
    """``_turn``, with the answer sent as it is written.

    Server-sent events, in order:

    * ``delta`` ``{"text": ...}`` — a fragment of the answer, zero or more;
    * then exactly one of ``done`` (the full ``BernieTurnResponse``, whose
      ``answer`` is the saved text and replaces the fragments shown so far) or
      ``error`` ``{"reason", "message"}`` (the same reasons as the 503 detail).

    Checks that can fail before the model is called (auth, budget, an unknown
    thread, an empty question) still fail as ordinary HTTP errors, because
    they run before this response starts.
    """
    question = clamp_question(question)
    if not question:
        raise HTTPException(status_code=422, detail="question is empty")

    async def events() -> AsyncIterator[str]:
        history = history_for_prompt(thread)
        await store.append(thread, Role.USER, question)
        fragments: List[str] = []
        try:
            async for fragment in bernie.converse_stream(question, facts, history=history):
                fragments.append(fragment)
                yield _sse("delta", {"text": fragment})
        except BernieUnavailable as exc:
            yield _sse("error", {
                "reason": getattr(exc, "reason", "unavailable"), "message": str(exc),
            })
            return

        # A leaked reasoning trace can only be recognised in the whole text.
        answer, reasoning = split_leaked_reasoning("".join(fragments).strip())
        answer = answer.strip()
        if not answer:
            yield _sse("error", {"reason": "empty", "message": "empty response"})
            return
        await store.append(thread, Role.ASSISTANT, answer, reasoning=reasoning or None)
        yield _sse("done", BernieTurnResponse(
            thread_id=thread.thread_id,
            answer=answer,
            disclaimer=DISCLAIMER,
            grounded_on=facts,
            turn_count=len(thread.turns),
        ).model_dump(mode="json"))

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # Proxies (Caddy, nginx) must pass fragments on, not hold them.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
