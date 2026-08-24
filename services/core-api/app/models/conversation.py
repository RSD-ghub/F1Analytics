"""Bernie's conversation state.

A strategist you can only ask one question is a lookup table. Conversation is
what makes follow-ups work — "why?", "what about Norris?", "so should they pit
early?" — and each of those is meaningless without the turn before it.

Threads are scoped to a race weekend. That is a deliberate limit rather than a
missing feature: Bernie's facts come from one weekend's structured record, and a
thread that wandered across races would accumulate context its grounding cannot
support.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class Turn(BaseModel):
    role: Role
    content: str
    created_at: datetime
    #: Reasoning trace, kept for auditing and never returned to the browser.
    #: It is where the model speculates en route to an answer, which is exactly
    #: the material a no-unsourced-claims product must not publish.
    reasoning: Optional[str] = None


class Thread(BaseModel):
    thread_id: str
    user_id: str
    season: int
    round: int
    race_name: str = ""
    created_at: datetime
    updated_at: datetime
    turns: List[Turn] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.turns


class ThreadSummary(BaseModel):
    """Listing shape — the turns themselves are fetched per thread."""

    thread_id: str
    season: int
    round: int
    race_name: str = ""
    updated_at: datetime
    turn_count: int = 0
    opening_question: str = ""


class AskRequest(BaseModel):
    question: str


class StartThreadRequest(BaseModel):
    season: int
    round: int
    question: str


class BernieTurnResponse(BaseModel):
    thread_id: str
    answer: str
    disclaimer: str
    #: The structured facts this turn was allowed to use. Returned every turn,
    #: not just the first, because they are re-derived every turn.
    grounded_on: Dict[str, Any] = Field(default_factory=dict)
    turn_count: int = 0
