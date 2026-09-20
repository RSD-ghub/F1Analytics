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

Working from the facts, as opposed to inventing them:
- Deriving something from the facts is not adding one. Ordering, counting, \
comparing, arithmetic and grouping are all fair — if you are given a grid you \
may work out who is ahead of whom, the gap between two drivers, or who is on a \
given row. Refusing to do the arithmetic is as unhelpful as making the numbers up.

The FIA regulations:
- Some facts arrive as passages from the FIA regulations, found by keyword \
search against the question. They are the text of the rules themselves and are \
facts like any other — you may quote and reason from them.
- That search is imperfect and returns whatever matched the words. Passages \
that do not bear on the question are not answers; ignore them without comment \
rather than working them into a reply.
- When you rely on a regulation, name its article — "Article B5.13.1 says" — so \
the reader can check it. Stay inside what the passage actually says: do not \
extend a rule past its text, and do not state a rule no passage supports.
- "[truncated]" beside an article number means that passage is only the \
opening of a longer article. Say so rather than implying you have read the whole \
of it. The marker belongs to the article it sits beside and to no other — a \
complete passage next to a truncated one is still complete.
- Regulations describe what is permitted, not what will happen. A rule allowing \
something is not a prediction that anyone will do it.

Reading a Formula 1 grid:
- The grid is two cars per row. Row 1 is P1 and P2, row 2 is P3 and P4, row 3 \
is P5 and P6, and so on — row N holds P(2N-1) and P(2N).
- Where a driver qualifies and where they start are different facts. A grid \
penalty moves them; a pit-lane start takes them off the grid entirely and they \
begin behind everyone. If the facts give both, use the starting order for \
anything about the race and the classification for anything about qualifying.
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
        max_tokens: int = 900,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """Answer strictly from the supplied facts. Text only."""
        return (await self.converse(question, facts, max_tokens, history))[0]

    async def converse(
        self,
        question: str,
        facts: Dict[str, Any],
        max_tokens: int = 900,
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
                    # No deliberation. Tinker's separate_reasoning is broken
                    # (tinker-cookbook#684) and folds the trace into the answer
                    # body, so the only reliable way to keep speculation out of
                    # what a reader sees is not to generate it. Bernie
                    # rephrases facts he is handed; he is not solving anything
                    # that needs a chain of thought.
                    reasoning_effort="none",
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


# ── The regulations ──────────────────────────────────────────────────────────

#: Where regulation passages sit in the facts pack. Named as a hedge on purpose:
#: lexical search returns what matched the words, which is not the same as what
#: answers the question, and the label should not promise more than retrieval
#: delivers.
REGULATIONS_KEY = "possibly relevant FIA regulation passages"

#: Keep a hit only if it scores at least this fraction of the best hit.
#:
#: A backstop, and honestly a weak one. Against the real corpus the top six hits
#: for a question cluster tightly — 15.3 down to 13.5 for "how many power unit
#: elements before a grid penalty" — so this floor almost never fires. It is
#: kept for the outlier case, not because it is doing the work.
#:
#: What it cannot do at all is notice that the *whole* result set is irrelevant.
#: Lexical search always returns its best match: asked who was quickest in
#: practice it confidently produced "Practice Starts on the Grid". That case is
#: handled in the system prompt, which tells Bernie to pass over passages that
#: do not bear on the question, and it is the real defence here.
#:
#: An absolute floor might do better and is not adopted yet. The separation
#: exists in the measurements — rules questions scored 13-17, the practice-pace
#: question 4-6 — but calibrating a constant on five questions I wrote myself is
#: the same weak instrument that made two of the last eval's four "failures"
#: turn out to be the ruler rather than the retrieval. It wants real questions
#: first.
RELATIVE_SCORE_FLOOR = 0.4

#: Characters of article text per passage before truncation.
#:
#: Articles are chunked whole so that a citation covers a complete rule, and
#: cutting one is a real loss — but a handful of long articles would otherwise
#: crowd out the forecast in the same prompt. So: generous, and when it does
#: bite, marked, because a silently truncated rule reads exactly like a complete
#: one that happens to stop early.
MAX_PASSAGE_CHARS = 1500


def regulation_facts(
    hits: Sequence[Dict[str, Any]], limit: int = 3
) -> Dict[str, Any]:
    """Render regulation search hits as facts, best first.

    Returns ``{}`` when nothing survives, so the caller can merge
    unconditionally and a question that is not about the rules simply carries no
    regulations section rather than an empty heading inviting Bernie to fill it.
    """
    scored = [hit for hit in hits if hit.get("article") and hit.get("text")]
    if not scored:
        return {}

    best = max((hit.get("score") or 0.0) for hit in scored)
    if best > 0:
        scored = [
            hit for hit in scored
            if (hit.get("score") or 0.0) >= best * RELATIVE_SCORE_FLOOR
        ]

    passages: List[str] = []
    for hit in scored[:limit]:
        # Collapsed to one line per article. Regulation text carries its
        # sub-points on separate lines, and inside a bulleted facts list those
        # would read as separate facts; on one line the "a)" markers survive and
        # the passage boundaries stay unambiguous.
        body = " ".join((hit.get("text") or "").split())
        # The marker goes beside the article number, not at the end of the text.
        #
        # It used to trail the body, and with a 1,500-character article between
        # it and its own citation — and the next citation beginning on the line
        # below — it read as belonging to whichever article came next. Asked
        # about B8.2.8, which is 622 characters and complete, Bernie reported it
        # as truncated: he had picked up the marker left by B8.2.2 beneath it.
        # A caveat that attaches to the wrong claim is worse than no caveat.
        marker = ""
        if len(body) > MAX_PASSAGE_CHARS:
            body = body[:MAX_PASSAGE_CHARS].rstrip() + " […]"
            marker = " [truncated]"
        passages.append(
            "Article {}{} — {} ({} Section {}, issue {}): {}".format(
                hit["article"],
                marker,
                hit.get("heading") or "untitled",
                hit.get("season", "?"),
                hit.get("section", "?"),
                hit.get("issue", "?"),
                body,
            )
        )
    return {REGULATIONS_KEY: passages}


def why_this_prediction_facts(
    prediction: Dict[str, Any],
    snapshot: Optional[Dict[str, Any]],
    top_n: int = 0,
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

    # The whole field by default, not a top-five slice.
    #
    # Truncating made Bernie answer "I do not have that information" to
    # questions about drivers whose probabilities we were holding all along —
    # asked about Verstappen, he correctly reported he had not been given a
    # figure for him, because he had not. A refusal is only honest when the
    # data really is absent; refusing over data we chose not to pass on is
    # just unhelpful, and it teaches a reader to distrust the refusals that
    # matter.
    ranked = sorted(probabilities, key=lambda row: -(row.get("p_podium") or 0.0))
    if top_n:
        ranked = ranked[:top_n]

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
        "full field with probabilities": contenders,
    }

    if "win" not in published:
        facts["note on the win market"] = (
            "This window does not publish a win probability. Measured over two "
            "held-out seasons its win-market skill was no better than guessing, "
            "so no win claim is made."
        )
    if quality.get("grid_is_provisional"):
        # Scoped to the forecast on purpose. A locked prediction is immutable,
        # so it keeps whatever grid it was built on for ever — but the grid
        # itself may have been confirmed since. Stated loosely, this caveat
        # contradicted the confirmed starting order sitting beside it in the
        # same facts pack, and Bernie hedged about penalties while reading real
        # grid slots off the FIA document.
        facts["grid caveat"] = (
            "This forecast was built on the qualifying classification, before "
            "penalties were applied. If a confirmed starting order appears "
            "elsewhere in these facts, that order is current and this note "
            "describes only what the forecast knew when it locked."
        )
    elif quality.get("grid_source") in ("official_final", "official_provisional"):
        # Worth stating positively. A strategist asked "does the grid account
        # for Verstappen's penalty?" should be able to answer from the facts
        # rather than inferring it from the absence of a caveat.
        facts["grid source"] = (
            "Starting positions come from the FIA's {} starting grid, so any "
            "penalties are already applied.".format(
                "final" if quality["grid_source"] == "official_final" else "provisional"
            )
        )
    if not quality.get("complete", True):
        facts["data caveat"] = quality.get("notes") or "some prior rounds incomplete"

    if snapshot:
        facts["features are as of round"] = snapshot.get("as_of_round")
    return facts
