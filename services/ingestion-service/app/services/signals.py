"""Structured signals extracted from unstructured race data.

Race control messages are free text — penalties, investigations, track limits,
safety cars, weather calls. They carry information the tabular pipeline cannot
see, and they are the one place an LLM measurably improves forecast accuracy
rather than merely narrating it.

Two hard rules:

**Signals are enrichment, never completeness.** A session with no extracted
signals is still ``COMPLETE``. Wiring the LLM into the completeness guarantee
would make the dataset's integrity depend on a third-party API's uptime, and on
a model's willingness to return valid JSON.

**Signals carry an as-of timestamp.** A signal extracted from Round 12's race
control log describes events during Round 12 and must never feed a forecast for
Round 12. Every stored signal records the round it describes, so the
point-in-time filter in prediction-service can exclude it the same way it
excludes results.

Rule-based extraction runs first and always. It is deterministic, free, and
catches the high-frequency categories; the LLM handles the long tail. A signal's
``source`` field records which produced it, so their contributions can be
compared rather than assumed.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from app.models.schemas import RaceControlRow
from f1_common.llm import LLMClient

logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_REQUEST = 200


class ExtractedSignal(BaseModel):
    """One structured fact derived from unstructured race data."""

    id: str
    season: int
    round: int
    category: str          # penalty | safety_car | red_flag | investigation | weather | other
    driver: str = ""
    detail: str = ""
    lap: int = 0
    #: Which round this signal *describes* — the point-in-time boundary.
    describes_round: int = 0
    source: str = "rule"   # rule | llm
    extracted_at: Optional[datetime] = None


#: Ordered most-specific first: a message about a penalty for causing a collision
#: should land as a penalty, not get swallowed by the collision pattern.
RULES = [
    ("penalty", re.compile(r"\b(\d+)\s*SECOND\s*(TIME\s*)?PENALTY\b", re.I)),
    ("penalty", re.compile(r"\bDRIVE\s*THROUGH\s*PENALTY\b", re.I)),
    ("penalty", re.compile(r"\bSTOP\s*(AND|/)\s*GO\b", re.I)),
    ("penalty", re.compile(r"\bGRID\s*PENALTY\b", re.I)),
    ("investigation", re.compile(r"\bUNDER\s*INVESTIGATION\b", re.I)),
    ("investigation", re.compile(r"\bNOTED\b", re.I)),
    ("red_flag", re.compile(r"\bRED\s*FLAG\b", re.I)),
    ("safety_car", re.compile(r"\bVIRTUAL\s*SAFETY\s*CAR\b|\bVSC\b", re.I)),
    ("safety_car", re.compile(r"\bSAFETY\s*CAR\b", re.I)),
    ("weather", re.compile(r"\bRAIN\b|\bWET\s*(TRACK|CONDITIONS)\b|\bSLIPPERY\b", re.I)),
    ("track_limits", re.compile(r"\bTRACK\s*LIMITS\b", re.I)),
]

#: "CAR 44 (HAM)" — the standard race-control driver reference.
DRIVER_PATTERN = re.compile(r"\bCAR\s*(\d+)\s*\(([A-Z]{3})\)", re.I)


def extract_rule_based(
    season: int, round_number: int, messages: Sequence[RaceControlRow]
) -> List[ExtractedSignal]:
    """Deterministic extraction. Always runs, never fails, costs nothing."""
    signals: List[ExtractedSignal] = []
    now = datetime.now(timezone.utc)

    for index, message in enumerate(messages):
        text = message.message or ""
        if not text.strip():
            continue

        category = _categorise(text)
        if category is None:
            continue

        driver_match = DRIVER_PATTERN.search(text)
        signals.append(
            ExtractedSignal(
                id="{}-{}-rule-{}".format(season, round_number, index),
                season=season,
                round=round_number,
                describes_round=round_number,
                category=category,
                driver=driver_match.group(2).upper() if driver_match else "",
                detail=text.strip()[:500],
                lap=message.lap,
                source="rule",
                extracted_at=now,
            )
        )
    return signals


def _categorise(text: str) -> Optional[str]:
    for category, pattern in RULES:
        if pattern.search(text):
            return category
    return None


async def extract_with_llm(
    client: LLMClient,
    season: int,
    round_number: int,
    messages: Sequence[RaceControlRow],
) -> List[ExtractedSignal]:
    """LLM extraction for the long tail the regexes miss.

    Every failure mode here returns an empty list rather than raising: no
    provider configured, network error, malformed JSON, unexpected shape. The
    caller must be free to treat this as best-effort, because the alternative is
    an ingest pipeline whose success depends on a model's formatting.
    """
    if not getattr(client, "available", False) or not messages:
        return []

    sample = [m.message for m in messages[:MAX_MESSAGES_PER_REQUEST] if m.message]
    if not sample:
        return []

    try:
        payload = await client.complete_json(
            prompt=_build_prompt(sample),
            system=(
                "You extract structured facts from Formula 1 race control messages. "
                "Return only JSON. Never infer facts that are not stated in the text."
            ),
            max_tokens=2048,
        )
    except Exception as exc:
        logger.warning(
            "LLM signal extraction failed for %s-%s: %s", season, round_number, exc
        )
        return []

    return _parse_signals(payload, season, round_number)


def _build_prompt(messages: Sequence[str]) -> str:
    numbered = "\n".join("{}. {}".format(i + 1, m) for i, m in enumerate(messages))
    return (
        "Extract notable events from these Formula 1 race control messages.\n\n"
        "Return a JSON array. Each element must have exactly these keys:\n"
        '  "category": one of penalty, safety_car, red_flag, investigation, '
        "weather, track_limits, other\n"
        '  "driver": three-letter driver code, or "" if the message names no driver\n'
        '  "detail": one short factual sentence\n'
        '  "lap": integer lap number, or 0 if not stated\n\n'
        "Include only events actually described in the messages. "
        "If nothing qualifies, return [].\n\n"
        "Messages:\n{}".format(numbered)
    )


def _parse_signals(
    payload: Any, season: int, round_number: int
) -> List[ExtractedSignal]:
    """Defensively map model output into signals.

    Anything malformed is skipped individually, so one bad element does not
    discard the whole extraction.
    """
    if not isinstance(payload, list):
        if payload is not None:
            logger.warning(
                "LLM returned %s, expected a list, for %s-%s",
                type(payload).__name__,
                season,
                round_number,
            )
        return []

    now = datetime.now(timezone.utc)
    signals: List[ExtractedSignal] = []

    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        detail = str(item.get("detail") or "").strip()
        if not detail:
            continue
        try:
            lap = int(item.get("lap") or 0)
        except (TypeError, ValueError):
            lap = 0

        signals.append(
            ExtractedSignal(
                id="{}-{}-llm-{}".format(season, round_number, index),
                season=season,
                round=round_number,
                describes_round=round_number,
                category=str(item.get("category") or "other").strip().lower(),
                driver=str(item.get("driver") or "").strip().upper()[:3],
                detail=detail[:500],
                lap=lap,
                source="llm",
                extracted_at=now,
            )
        )
    return signals


def summarise(signals: Sequence[ExtractedSignal]) -> Dict[str, int]:
    """Signal counts by category — the shape a feature builder consumes."""
    counts: Dict[str, int] = {}
    for signal in signals:
        counts[signal.category] = counts.get(signal.category, 0) + 1
    return counts
