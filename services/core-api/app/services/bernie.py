"""Bernie — the pit-wall strategist voice.

Inspired by the real-world race strategists who talk an audience through what a
team is actually weighing. **Not a person.** Every response carries a visible
disclaimer, and the persona is described by role rather than by imitating any
named individual's speech, opinions or claimed experience.

The rule that makes Bernie safe to attach to a forecasting product:

    **Bernie may rephrase facts he is given. He may never introduce one.**

A product whose entire promise is calibrated honesty cannot have a language
model inventing an upgrade package next to its probabilities. So every prompt
carries the facts explicitly, the system prompt forbids going beyond them, and
when no facts are available Bernie says he does not know rather than filling the
gap — which is what a good strategist does anyway.
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from f1_common.llm import LLMBillingRequired, LLMClient

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "Bernie is an AI pit-wall strategist inspired by the role of a real race "
    "strategist. He is not a real person and does not represent anyone."
)

SYSTEM_PROMPT = """You are Bernie, an AI pit-wall race strategist for a Formula 1 \
forecasting service.

You explain what the numbers in front of you mean, the way a strategist talks a \
team through a decision: concrete, unhurried, and honest about uncertainty.

Absolute rules:
- Use ONLY the facts given to you in the CURRENT message under "facts". Never \
add a fact that is not there — no lap times, no upgrades, no results, no \
quotes, no names of people.
- Earlier messages in this conversation are context for what is being ASKED. \
They are NOT a source of facts. If something you said earlier is not in the \
current facts, do not rely on it or repeat it as established.
- If the facts do not answer the question, say plainly that you do not have \
that information. Do not speculate to fill the gap.
- Probabilities are probabilities. Never restate a 60% chance as a certainty, \
and never call a low-probability outcome impossible.
- You are not a real person. Do not claim experience, memories, or opinions \
about real individuals.
- Two or three short paragraphs at most. No preamble.
"""


class BernieUnavailable(RuntimeError):
    """Bernie cannot answer. Callers degrade to the structured facts alone.

    Carries ``reason`` so an operator can tell apart the cases that look
    identical from the outside: not configured, out of credit, provider down.
    All three degrade the same way for the reader; only one of them is fixed by
    touching the config.
    """

    def __init__(self, message: str, reason: str = "unavailable") -> None:
        super().__init__(message)
        self.reason = reason


def _render_facts(facts: Dict[str, Any]) -> str:
    lines: List[str] = []
    for key, value in facts.items():
        if value is None or value == [] or value == {}:
            continue
        if isinstance(value, (list, tuple)):
            lines.append("{}:".format(key))
            lines.extend("  - {}".format(item) for item in value)
        else:
            lines.append("{}: {}".format(key, value))
    return "\n".join(lines) if lines else "(no facts available)"


class Bernie:
    def __init__(self, client: LLMClient) -> None:
        self._client = client

    @property
    def available(self) -> bool:
        return bool(getattr(self._client, "available", False))

    async def explain(
        self,
        question: str,
        facts: Dict[str, Any],
        max_tokens: int = 700,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """Answer strictly from the supplied facts. Text only."""
        return (await self.converse(question, facts, max_tokens, history))[0]

    async def converse(
        self,
        question: str,
        facts: Dict[str, Any],
        max_tokens: int = 700,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[str, Optional[str]]:
        """A conversational turn: returns ``(answer, reasoning_trace)``.

        ``facts`` are re-derived by the caller on every turn and injected fresh.
        ``history`` supplies continuity for what is being *asked*, never for what
        is *true* — the system prompt is explicit that prior replies are not a
        fact source, which is what stops a thread compounding its own errors.

        Raises ``BernieUnavailable`` when no provider is configured, so the
        caller can fall back to showing the facts unnarrated rather than
        rendering an empty panel.
        """
        if not self.available:
            raise BernieUnavailable(
                "no LLM provider configured", reason="not_configured"
            )

        prompt = (
            "Question: {}\n\n"
            "facts (the only information you may use for this answer):\n{}\n"
        ).format(question, _render_facts(facts))

        try:
            # complete_verbose returns the reasoning trace separately where the
            # provider supports it; fall back for clients that do not.
            if hasattr(self._client, "complete_verbose"):
                completion = await self._client.complete_verbose(
                    prompt=prompt,
                    system=SYSTEM_PROMPT,
                    max_tokens=max_tokens,
                    # Low but not zero: strategist prose reads better with a
                    # little variation, and nothing downstream depends on it
                    # being reproducible — unlike the probabilities, which are
                    # seeded.
                    temperature=0.3,
                    history=history,
                )
                answer, reasoning = completion.text, completion.reasoning
            else:
                answer = await self._client.complete(
                    prompt=prompt,
                    system=SYSTEM_PROMPT,
                    max_tokens=max_tokens,
                    temperature=0.3,
                )
                reasoning = None
        except LLMBillingRequired as exc:
            logger.error("Bernie is out of credit: %s", exc)
            raise BernieUnavailable(str(exc), reason="billing") from exc
        except Exception as exc:
            logger.warning("Bernie call failed: %s", exc)
            raise BernieUnavailable(str(exc), reason="provider_error") from exc

        if not (answer or "").strip():
            raise BernieUnavailable("empty response", reason="empty")
        return answer.strip(), reasoning


def why_this_prediction_facts(
    prediction: Dict[str, Any],
    snapshot: Optional[Dict[str, Any]],
    top_n: int = 5,
) -> Dict[str, Any]:
    """Assemble the facts behind one forecast, ready to hand to Bernie.

    Deliberately includes the data-quality flags. A forecast built on a
    provisional grid or an incomplete corpus is a forecast with a caveat, and
    the explanation should carry it rather than presenting the number as
    unqualified.
    """
    quality = prediction.get("data_quality") or {}
    probabilities = prediction.get("driver_probabilities") or []
    published = prediction.get("published_markets") or []

    ranked = sorted(
        probabilities, key=lambda row: -(row.get("p_podium") or 0.0)
    )[:top_n]

    contenders = []
    for row in ranked:
        parts = ["{} ({})".format(row.get("driver"), row.get("team", "?"))]
        if "win" in published and row.get("p_win") is not None:
            parts.append("win {:.0%}".format(row["p_win"]))
        parts.append("podium {:.0%}".format(row.get("p_podium") or 0.0))
        parts.append("points {:.0%}".format(row.get("p_points") or 0.0))
        contenders.append(" · ".join(parts))

    facts: Dict[str, Any] = {
        "race": "{} round {}".format(prediction.get("season"), prediction.get("round")),
        "forecast window": prediction.get("window"),
        "markets published": ", ".join(published) or "none",
        "model version": prediction.get("model_version"),
        "top contenders": contenders,
    }

    if "win" not in published:
        facts["note on the win market"] = (
            "This window does not publish a win probability. Measured over two "
            "held-out seasons its win-market skill was no better than guessing, "
            "so no win claim is made."
        )
    if quality.get("grid_is_provisional"):
        facts["grid caveat"] = (
            "Grid positions are qualifying classification; any penalties are "
            "not yet applied."
        )
    if not quality.get("complete", True):
        facts["data caveat"] = quality.get("notes") or "some prior rounds incomplete"

    if snapshot:
        facts["features are as of round"] = snapshot.get("as_of_round")
    return facts
