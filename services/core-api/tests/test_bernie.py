"""Bernie tests: the Tinker contract, and staying grounded across turns.

Two distinct risks. The first is mechanical — an earlier version of the client
was wrong about Tinker's base URL, endpoint path and model name simultaneously,
and nothing in the codebase would have noticed until a live call 404'd.

The second is the one that matters for the product. A conversational agent
attached to calibrated probabilities can drift: turn one says something the
record does not support, turn three treats it as established, and by turn five
the thread is fiction told confidently. The defence is that facts are re-derived
every turn and prior replies are explicitly not a fact source.
"""

import pytest

from app.models.conversation import Role, Thread, Turn
from app.services.bernie import (
    DISCLAIMER,
    REGULATIONS_KEY,
    SYSTEM_PROMPT,
    Bernie,
    BernieUnavailable,
    regulation_facts,
    why_this_prediction_facts,
)
from app.services.conversation import (
    HISTORY_TURNS,
    MAX_QUESTION_CHARS,
    clamp_question,
    history_for_prompt,
)
from f1_common.llm import TinkerClient, build_client

TINKER_BASE = "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1"


# ── The Tinker wire contract ─────────────────────────────────────────────────


def _client():
    return TinkerClient("key", TINKER_BASE, "thinkingmachines/Inkling")


def test_the_endpoint_path_is_not_double_versioned():
    """Tinker's base URL already ends in /api/v1.

    Appending /v1/chat/completions — the obvious guess, and what this code did
    originally — produces a 404 against a live endpoint.
    """
    client = _client()
    assert client._path == "/chat/completions"
    assert not client._path.startswith("/v1")


def test_the_model_is_addressed_by_its_namespaced_name():
    payload = _client()._build_request("q", None, 100, 0.2)
    assert payload["model"] == "thinkingmachines/Inkling"


def test_reasoning_effort_is_sent_explicitly():
    """Tinker defaults to 0.9, which is slow and costly for rephrasing facts."""
    payload = _client()._build_request("q", None, 100, 0.2)
    assert payload["reasoning_effort"] == "medium"


def test_reasoning_effort_can_be_overridden_per_call():
    payload = _client()._build_request("q", None, 100, 0.2, reasoning_effort="low")
    assert payload["reasoning_effort"] == "low"


def test_reasoning_is_requested_separately():
    """Without this Tinker folds thinking tokens into message.content, and the
    deliberation gets rendered to the reader as if it were the reply."""
    payload = _client()._build_request("q", None, 100, 0.2)
    assert payload["extra_body"]["separate_reasoning"] is True


def test_history_sits_between_the_system_prompt_and_the_question():
    payload = _client()._build_request(
        "now", "sys", 100, 0.2,
        history=[{"role": "user", "content": "before"},
                 {"role": "assistant", "content": "reply"}],
    )
    assert [m["role"] for m in payload["messages"]] == [
        "system", "user", "assistant", "user",
    ]
    assert payload["messages"][-1]["content"] == "now"


def test_the_response_separates_answer_from_reasoning():
    completion = TinkerClient._parse_completion({
        "choices": [{"message": {
            "content": "  The answer.  ",
            "reasoning_content": "internal deliberation",
        }}]
    })
    assert completion.text == "The answer."
    assert completion.reasoning == "internal deliberation"


def test_an_empty_choices_array_does_not_explode():
    assert TinkerClient._parse_completion({"choices": []}).text == ""


def test_no_api_key_yields_a_null_client():
    assert not build_client("tinker", "", TINKER_BASE, "thinkingmachines/Inkling").available


# ── Grounding across turns ───────────────────────────────────────────────────


def test_the_system_prompt_forbids_treating_replies_as_facts():
    """The rule that stops a thread compounding its own errors."""
    assert "NOT a source of facts" in SYSTEM_PROMPT
    assert "current facts" in SYSTEM_PROMPT


def test_the_system_prompt_forbids_impersonation():
    assert "not a real person" in SYSTEM_PROMPT.lower()


def test_the_disclaimer_says_bernie_is_not_a_person():
    assert "not a real person" in DISCLAIMER.lower()


class RecordingLLM:
    available = True

    def __init__(self, reply="Understood."):
        self.reply = reply
        self.calls = []

    async def complete_verbose(self, prompt, system=None, max_tokens=1024,
                               temperature=0.2, history=None, reasoning_effort=None):
        from f1_common.llm import Completion
        self.calls.append({"prompt": prompt, "system": system, "history": history})
        return Completion(text=self.reply, reasoning="thought about it")

    async def complete(self, prompt, system=None, max_tokens=1024, temperature=0.2):
        return self.reply


async def test_facts_are_labelled_as_the_only_permitted_source():
    llm = RecordingLLM()
    await Bernie(llm).converse("Who is quickest?", {"practice": "Alpha fastest"})

    prompt = llm.calls[0]["prompt"]
    assert "only information you may use" in prompt
    assert "Alpha fastest" in prompt


async def test_history_is_passed_but_facts_are_re_injected():
    """Continuity for the question; grounding from the record, every turn."""
    llm = RecordingLLM()
    history = [{"role": "user", "content": "and Norris?"}]
    await Bernie(llm).converse("Why?", {"forecast": "Alpha 40%"}, history=history)

    call = llm.calls[0]
    assert call["history"] == history
    assert "Alpha 40%" in call["prompt"]


async def test_the_reasoning_trace_is_returned_separately_from_the_answer():
    answer, reasoning = await Bernie(RecordingLLM("The answer.")).converse("q", {})

    assert answer == "The answer."
    assert reasoning == "thought about it"


async def test_no_provider_raises_rather_than_inventing():
    class Off:
        available = False

    with pytest.raises(BernieUnavailable):
        await Bernie(Off()).converse("q", {})


async def test_an_empty_reply_is_treated_as_a_failure():
    """Silence rendered as an answer is worse than a visible unavailability."""
    with pytest.raises(BernieUnavailable):
        await Bernie(RecordingLLM("   ")).converse("q", {})


# ── History assembly ─────────────────────────────────────────────────────────


def _thread(n):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return Thread(
        thread_id="t", user_id="u", season=2026, round=5,
        created_at=now, updated_at=now,
        turns=[
            Turn(role=Role.USER if i % 2 == 0 else Role.ASSISTANT,
                 content="m{}".format(i), created_at=now, reasoning="secret")
            for i in range(n)
        ],
    )


def test_history_is_bounded():
    """An unbounded thread eventually silently overruns the context window."""
    assert len(history_for_prompt(_thread(50))) == HISTORY_TURNS


def test_history_keeps_the_most_recent_turns():
    messages = history_for_prompt(_thread(10))
    assert messages[-1]["content"] == "m9"


def test_reasoning_traces_are_never_replayed_to_the_model():
    """Feeding a model its own speculation back amplifies it."""
    for message in history_for_prompt(_thread(4)):
        assert set(message) == {"role", "content"}
        assert "secret" not in message["content"]


def test_an_overlong_question_is_truncated_not_rejected():
    clamped = clamp_question("x" * (MAX_QUESTION_CHARS + 500))
    assert len(clamped) == MAX_QUESTION_CHARS


def test_whitespace_only_questions_collapse_to_empty():
    assert clamp_question("   \n  ") == ""


# ── Billing (402) ────────────────────────────────────────────────────────────


def test_billing_error_is_a_kind_of_unavailable():
    """Subclassing keeps every existing degradation path working untouched."""
    from f1_common.llm import LLMBillingRequired, LLMUnavailable

    assert issubclass(LLMBillingRequired, LLMUnavailable)


async def test_a_402_is_reported_as_billing_not_a_generic_failure():
    """402 is the one failure that proves the config is *right*.

    The key authenticated, the endpoint resolved, the model name was accepted —
    only credit is missing. Flattening it into "unavailable" sends an operator
    back to re-check a URL that is fine.
    """
    from f1_common.llm import LLMBillingRequired

    class OutOfCredit:
        available = True

        async def complete_verbose(self, *a, **k):
            raise LLMBillingRequired("402 Payment Required")

        async def complete(self, *a, **k):
            raise LLMBillingRequired("402 Payment Required")

    with pytest.raises(BernieUnavailable) as caught:
        await Bernie(OutOfCredit()).converse("q", {})

    assert caught.value.reason == "billing"
    assert "402" in str(caught.value)


async def test_the_reasons_are_distinguishable():
    """Three failures that look identical to a reader, and are not to an operator."""
    from f1_common.llm import LLMBillingRequired

    class NotConfigured:
        available = False

    class Broken:
        available = True

        async def complete_verbose(self, *a, **k):
            raise RuntimeError("connection reset")

    async def reason_for(client):
        try:
            await Bernie(client).converse("q", {})
        except BernieUnavailable as exc:
            return exc.reason
        return None

    assert await reason_for(NotConfigured()) == "not_configured"
    assert await reason_for(Broken()) == "provider_error"


async def test_the_blog_still_renders_when_bernie_is_out_of_credit():
    """The facts are the substance. Narration is polish, and its absence must
    never cost the reader the entry."""
    from f1_common.llm import LLMBillingRequired
    from app.services import blog as builder
    from tests.test_blog import FIELD

    class OutOfCredit:
        available = True

        async def complete_verbose(self, *a, **k):
            raise LLMBillingRequired("402 Payment Required")

        async def complete(self, *a, **k):
            raise LLMBillingRequired("402 Payment Required")

    entry = builder.practice_entry(2026, 5, "T", FIELD)
    narrated = await builder.narrate(Bernie(OutOfCredit()), entry)

    assert narrated.narrative is None
    assert narrated.facts
    assert narrated.table


# ── Leaked reasoning (tinker-cookbook#684) ───────────────────────────────────


def test_a_marked_reasoning_trace_is_split_from_the_answer():
    """Tinker folds thinking tokens into message.content and returns
    reasoning_content as None. Where the marker survives we can recover the
    answer; the real defence is asking for no reasoning at all."""
    from f1_common.llm import TinkerClient

    completion = TinkerClient._parse_completion({
        "choices": [{"message": {
            "content": "We need to compare the two.</think>Alpha was quicker.",
            "reasoning_content": None,
        }}]
    })

    assert completion.text == "Alpha was quicker."
    assert "We need to compare" in completion.reasoning


def test_unmarked_content_is_left_alone():
    """An unmarked trace is indistinguishable from prose. Guessing would risk
    truncating a genuine answer, which is worse than a verbose one."""
    from f1_common.llm import TinkerClient

    completion = TinkerClient._parse_completion({
        "choices": [{"message": {"content": "Alpha was quicker.", "reasoning_content": None}}]
    })

    assert completion.text == "Alpha was quicker."
    assert completion.reasoning is None


def test_bernie_asks_for_no_reasoning():
    """User-facing calls must not generate a trace that could leak into the
    answer body."""
    import inspect
    from app.services import bernie as module

    assert 'reasoning_effort="none"' in inspect.getsource(module.Bernie.converse)


def test_the_whole_field_is_given_to_bernie():
    """Truncating to a top-five slice made Bernie refuse questions about
    drivers whose probabilities we were holding. A refusal is only honest when
    the data is genuinely absent."""
    prediction = {
        "season": 2026, "round": 13, "window": "post_quali",
        "model_version": "v4", "published_markets": ["win", "podium", "points"],
        "data_quality": {"complete": True},
        "driver_probabilities": [
            {"driver": "D%d" % i, "team": "T", "p_win": 0.05,
             "p_podium": 0.5 - i * 0.02, "p_points": 0.8}
            for i in range(20)
        ],
    }
    facts = why_this_prediction_facts(prediction, None)

    assert len(facts["full field with probabilities"]) == 20
    assert any("D19" in row for row in facts["full field with probabilities"])


# ── Regulations retrieval ────────────────────────────────────────────────────
#
# Bernie is allowed to quote the rules because the rule text is handed to him as
# a fact, the same way probabilities are. What these guard is the gap between
# "the search returned something" and "the something is an answer": lexical
# retrieval always returns its best match, including for questions that are not
# about the rules at all.


def _hit(article, text="The relevant rule text.", score=5.0, heading="Heading"):
    return {
        "article": article, "heading": heading, "text": text, "score": score,
        "season": 2026, "section": "B", "issue": 8,
    }


def test_a_passage_arrives_with_the_citation_that_identifies_it():
    """Chunking by article exists so a claim can be traced. A passage rendered
    without its number throws that away at the last step."""
    facts = regulation_facts([_hit("B5.13.1", heading="Deployment of Safety Car")])

    passage = facts[REGULATIONS_KEY][0]
    assert "B5.13.1" in passage
    assert "Deployment of Safety Car" in passage
    assert "2026 Section B, issue 8" in passage


def test_the_weak_tail_is_dropped_relative_to_the_best_hit():
    """Every query returns its best match. What matters is whether the rest are
    in the same class as it or noise trailing behind it."""
    facts = regulation_facts(
        [_hit("B5.13.1", score=10.0), _hit("B1.8.4", score=6.0),
         _hit("C2.1.1", score=1.0)],
        limit=5,
    )

    rendered = " ".join(facts[REGULATIONS_KEY])
    assert "B5.13.1" in rendered and "B1.8.4" in rendered
    assert "C2.1.1" not in rendered


def test_no_hits_means_no_regulations_section_at_all():
    """An empty heading is an invitation to fill it. A question about practice
    pace should carry no rules section rather than an empty one."""
    assert regulation_facts([]) == {}


def test_a_hit_missing_its_text_is_not_rendered_as_a_rule():
    assert regulation_facts([{"article": "B1.1", "score": 9.0}]) == {}


def test_a_long_article_is_marked_where_it_is_cut():
    """A silently truncated rule reads exactly like a complete one that happens
    to stop early — which is how half a rule gets quoted as the whole of it."""
    facts = regulation_facts([_hit("B12.1", text="word " * 2000)])

    passage = facts[REGULATIONS_KEY][0]
    assert "[truncated]" in passage
    assert len(passage) < 2200


def test_a_short_article_is_not_marked_truncated():
    facts = regulation_facts([_hit("B12.1", text="A short rule.")])
    assert "[truncated]" not in facts[REGULATIONS_KEY][0]


def test_sub_points_stay_on_one_line_with_their_article():
    """Inside a bulleted facts list, an article broken across lines reads as
    several separate facts rather than one rule."""
    facts = regulation_facts([_hit("B2.1", text="Either:\na) one thing\nb) another")])

    passages = facts[REGULATIONS_KEY]
    assert len(passages) == 1
    assert "\n" not in passages[0]
    assert "a) one thing" in passages[0] and "b) another" in passages[0]


def test_only_the_top_few_passages_are_carried():
    """The forecast is the substance of the prompt; the rulebook must not crowd
    it out."""
    facts = regulation_facts([_hit("B%d.1" % i, score=9.0) for i in range(10)])
    assert len(facts[REGULATIONS_KEY]) == 3


async def test_regulation_text_reaches_the_model_as_a_fact():
    llm = RecordingLLM()
    facts = {"race": "2026 round 5"}
    facts.update(regulation_facts([_hit("B5.13.1", text="The safety car may be deployed.")]))

    await Bernie(llm).converse("When does the safety car come out?", facts)

    prompt = llm.calls[0]["prompt"]
    assert "B5.13.1" in prompt
    assert "The safety car may be deployed." in prompt


def test_the_system_prompt_requires_the_article_to_be_named():
    assert "name its article" in SYSTEM_PROMPT


def test_the_system_prompt_permits_ignoring_irrelevant_passages():
    """Retrieval hands Bernie its best match for every question, including the
    ones that are not about the rules. Without this he would be under pressure
    to use whatever arrived."""
    assert "do not bear on the question" in SYSTEM_PROMPT
    assert "ignore them without comment" in SYSTEM_PROMPT


def test_the_system_prompt_forbids_extending_a_rule_past_its_text():
    assert "do not extend a rule past its text" in SYSTEM_PROMPT


def test_the_truncation_marker_sits_beside_the_article_it_describes():
    """It used to trail the body. With a long article between it and its own
    citation, and the next citation starting below, it read as belonging to the
    following article: asked about B8.2.8 — complete at 622 characters — Bernie
    reported it truncated, having picked up the marker left by B8.2.2."""
    facts = regulation_facts(
        [_hit("B8.2.8", text="A complete rule.", score=9.0),
         _hit("B8.2.2", text="word " * 2000, score=8.0)],
        limit=2,
    )

    complete, truncated = facts[REGULATIONS_KEY]
    assert "[truncated]" not in complete
    assert truncated.startswith("Article B8.2.2 [truncated]")


def test_the_system_prompt_scopes_the_marker_to_its_own_article():
    assert "belongs to the article it sits beside and to no other" in SYSTEM_PROMPT
