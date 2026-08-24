"""Multi-turn conversation storage and history assembly.

The rule that makes a fact-grounded agent survive more than one turn:

    **Facts are re-derived from the structured record on every turn. Bernie's
    own previous answers are never a source of facts.**

Without that, a conversation compounds its own errors. If turn one says
something the record does not support, turn three treats it as established
context and builds on it — and by turn five the thread has drifted into fiction
while every individual reply looked reasonable. Re-grounding each turn means an
error can survive one reply, not a conversation.

History is bounded for the same reason it is bounded anywhere: a thread that
grows without limit eventually exceeds the context window, and the failure mode
is a silent truncation of whichever end the provider chooses.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from app.models.conversation import Role, Thread, ThreadSummary, Turn

logger = logging.getLogger(__name__)

THREADS = "bernie_threads"

#: How many prior turns to replay. Six is three exchanges — enough for a
#: follow-up chain to make sense, short enough that the facts block stays the
#: dominant part of the prompt rather than being crowded out by chat history.
HISTORY_TURNS = 6

#: A single question long enough to be a prompt-injection payload rather than a
#: question. Truncating is friendlier than rejecting and removes the incentive.
MAX_QUESTION_CHARS = 2000


class ThreadNotFound(RuntimeError):
    """No such thread, or it belongs to someone else."""


class ConversationStore:
    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._db = database

    async def ensure_indexes(self) -> None:
        await self._db[THREADS].create_index(
            [("user_id", ASCENDING), ("updated_at", DESCENDING)]
        )

    async def create(
        self, user_id: str, season: int, round_number: int, race_name: str = ""
    ) -> Thread:
        now = datetime.now(timezone.utc)
        thread = Thread(
            thread_id=str(uuid.uuid4()),
            user_id=user_id,
            season=season,
            round=round_number,
            race_name=race_name,
            created_at=now,
            updated_at=now,
        )
        await self._db[THREADS].insert_one(
            dict(thread.model_dump(mode="json"), _id=thread.thread_id)
        )
        return thread

    async def get(self, thread_id: str, user_id: str) -> Thread:
        """Fetch a thread, scoped to its owner.

        The ``user_id`` filter is authorisation, not convenience: without it a
        valid token for any account could read any other account's
        conversations by guessing an id.
        """
        doc = await self._db[THREADS].find_one(
            {"_id": thread_id, "user_id": user_id}, projection={"_id": False}
        )
        if doc is None:
            raise ThreadNotFound("no thread {} for this account".format(thread_id))
        return Thread(**doc)

    async def append(
        self, thread: Thread, role: Role, content: str, reasoning: Optional[str] = None
    ) -> Thread:
        turn = Turn(
            role=role,
            content=content,
            created_at=datetime.now(timezone.utc),
            reasoning=reasoning,
        )
        thread.turns.append(turn)
        thread.updated_at = turn.created_at
        await self._db[THREADS].update_one(
            {"_id": thread.thread_id},
            {
                "$push": {"turns": turn.model_dump(mode="json")},
                "$set": {"updated_at": thread.updated_at},
            },
        )
        return thread

    async def list_for(self, user_id: str, limit: int = 25) -> List[ThreadSummary]:
        cursor = (
            self._db[THREADS]
            .find({"user_id": user_id}, projection={"_id": False})
            .sort([("updated_at", DESCENDING)])
            .limit(limit)
        )
        summaries: List[ThreadSummary] = []
        async for doc in cursor:
            turns = doc.get("turns") or []
            opening = next(
                (t.get("content", "") for t in turns if t.get("role") == "user"), ""
            )
            summaries.append(
                ThreadSummary(
                    thread_id=doc["thread_id"],
                    season=doc["season"],
                    round=doc["round"],
                    race_name=doc.get("race_name", ""),
                    updated_at=doc["updated_at"],
                    turn_count=len(turns),
                    opening_question=opening[:120],
                )
            )
        return summaries


def history_for_prompt(thread: Thread, limit: int = HISTORY_TURNS) -> List[Dict[str, str]]:
    """The last few turns, as chat messages.

    Reasoning traces are deliberately excluded. Replaying a model's own
    deliberation back to it amplifies whatever it speculated about, and the
    trace is not something the reader ever saw.
    """
    recent = thread.turns[-limit:]
    return [{"role": turn.role.value, "content": turn.content} for turn in recent]


def clamp_question(question: str) -> str:
    text = (question or "").strip()
    if len(text) > MAX_QUESTION_CHARS:
        logger.info("question truncated from %s chars", len(text))
        return text[:MAX_QUESTION_CHARS]
    return text
