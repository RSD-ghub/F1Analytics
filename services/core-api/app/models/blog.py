"""One Blog — the reader-facing weekend record.

A race weekend told as an append-only timeline: what practice showed, what
qualifying settled, what we forecast and why, and finally what happened. Each
entry is stamped when its facts became knowable, so the timeline doubles as the
point-in-time record — a reader can see exactly what was known at the moment a
forecast was committed, which is the same property the leakage tests enforce
internally.

Every entry carries ``facts`` (computed, structured) and ``sources`` (what they
were derived from). ``narrative`` is optional prose over those same facts and
never a source of new ones.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EntryKind(str, Enum):
    PRACTICE = "practice_report"
    QUALIFYING = "qualifying_report"
    FORECAST = "forecast"
    RESULT = "result"


class BlogFact(BaseModel):
    """One displayable claim, with the numbers behind it.

    Facts are structured rather than pre-formatted strings so the frontend can
    render a table, a chart or a sentence from the same entry without the API
    guessing which.
    """

    label: str
    value: str
    detail: str = ""


class BlogEntry(BaseModel):
    entry_id: str
    season: int
    round: int
    race_name: str = ""
    kind: EntryKind
    #: When the underlying facts became knowable — not when this was rendered.
    #: Ordering by this is what makes the timeline honest.
    occurred_at: Optional[datetime] = None
    headline: str
    summary: str = ""
    facts: List[BlogFact] = Field(default_factory=list)
    table: List[Dict[str, Any]] = Field(default_factory=list)
    #: Optional prose over the facts above. Absent when no LLM is configured,
    #: which degrades the page rather than breaking it.
    narrative: Optional[str] = None
    #: What this was computed from. An entry with no sources is not publishable.
    sources: List[str] = Field(default_factory=list)

    @property
    def is_sourced(self) -> bool:
        return bool(self.sources)


class WeekendBlog(BaseModel):
    season: int
    round: int
    race_name: str = ""
    circuit: str = ""
    entries: List[BlogEntry] = Field(default_factory=list)
    #: Set when narration was requested but no LLM is configured, so the UI can
    #: explain the absence instead of showing a silently plainer page.
    narration_available: bool = True
