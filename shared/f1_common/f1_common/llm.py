"""LLM access behind a swappable interface.

Inkling (Thinking Machines Lab), served via the Tinker API, is the configured
provider. Everything in the codebase depends on the ``LLMClient`` protocol rather
than on Inkling directly, so swapping provider is a config change plus one new
implementation — the domain code never learns which model answered.

**Wire format verified against Tinker's documentation** (August 2026). Tinker
exposes an OpenAI-compatible surface, but three details are not guessable and an
earlier version of this file got all three wrong:

* the base URL is ``https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1``
  — not an ``api.thinkingmachines.ai`` host;
* that base *already ends in* ``/api/v1``, so the path is ``/chat/completions``
  and appending ``/v1/...`` produces a 404;
* models are addressed as ``thinkingmachines/Inkling`` (or ``-Small``), not a
  bare ``inkling``.

Tinker also accepts ``reasoning_effort`` — "none" through "xhigh", or a raw
float in [0.0, 0.99] — which controls how long the model deliberates before
answering. It defaults to 0.9.

**``separate_reasoning`` does not work, so we do not rely on it.** Tinker's
OpenAI-compatible endpoint has a known open bug (tinker-cookbook#684) where
``reasoning_content`` comes back ``None`` and the thinking tokens are
concatenated into ``message.content`` instead — sometimes with the separator
markers stripped, which the maintainers themselves describe as leaving no
reliable way to parse them apart.

That is disqualifying for a product whose promise is that every published claim
traces to stored data: the reasoning trace is precisely where a model
speculates on its way to an answer. So the defence is to stop generating one —
callers that render text to a reader pass ``reasoning_effort="none"`` — with
marker stripping below only as a safety net for the cases where a trace appears
anyway.

Everything that uses this must degrade gracefully when no provider is configured.
LLM output is enrichment — explanations, extracted signals, Bernie's commentary.
It is never load-bearing for a probability or a completeness guarantee, so an
absent API key must reduce the product, not break it.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """No provider is configured, or the provider could not be reached."""


class LLMBillingRequired(LLMUnavailable):
    """The provider authenticated the request and refused it for payment.

    A subclass of ``LLMUnavailable`` so every existing degradation path keeps
    working untouched — but distinguishable, because it means something quite
    different from the other failures. A 402 proves the endpoint, the model name
    and the credential are all correct; only credit is missing. Collapsing it
    into a generic "unavailable" sends someone re-checking a URL that is fine.
    """


class Completion:
    """An answer, plus the reasoning trace when the model separates one."""

    __slots__ = ("text", "reasoning")

    def __init__(self, text: str, reasoning: Optional[str] = None) -> None:
        self.text = text
        self.reasoning = reasoning


@runtime_checkable
class LLMClient(Protocol):
    """The only LLM surface domain code may depend on."""

    @property
    def available(self) -> bool:
        """False when unconfigured — callers skip enrichment rather than fail."""
        ...

    async def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        ...

    async def complete_json(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> Any:
        ...


class NullClient:
    """Used when no provider is configured.

    Returns emptiness rather than raising on ``complete``: the calling code is
    enrichment, and enrichment that is switched off should be a no-op, not an
    error path every caller has to handle.
    """

    @property
    def available(self) -> bool:
        return False

    async def complete(self, prompt, system=None, max_tokens=1024, temperature=0.2) -> str:
        logger.debug("LLM unavailable; returning empty completion")
        return ""

    async def complete_json(self, prompt, system=None, max_tokens=1024) -> Any:
        return None


#: Tinker's documented reasoning-effort levels. Strings map to floats
#: internally (none=0.0, minimal=0.1, low=0.2, medium=0.7, high=0.9, xhigh=0.99).
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")


class TinkerClient:
    """Inkling via the Tinker API."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        completions_path: str = "/chat/completions",
        reasoning_effort: str = "medium",
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._path = completions_path
        self._reasoning_effort = reasoning_effort

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    async def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        history: Optional[List[Dict[str, str]]] = None,
        reasoning_effort: Optional[str] = None,
    ) -> str:
        """Single answer. ``history`` carries prior turns for a conversation."""
        return (
            await self.complete_verbose(
                prompt, system, max_tokens, temperature, history, reasoning_effort
            )
        ).text

    async def complete_verbose(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        history: Optional[List[Dict[str, str]]] = None,
        reasoning_effort: Optional[str] = None,
    ) -> "Completion":
        """Answer plus the separated reasoning trace, when the model returns one.

        The trace is captured for auditing rather than display. It is where a
        model speculates on its way to an answer, and surfacing it in a product
        that promises no unsourced claims would publish exactly the material
        that promise excludes.
        """
        if not self.available:
            raise LLMUnavailable("no Tinker API key configured")

        payload = self._build_request(
            prompt, system, max_tokens, temperature, history, reasoning_effort
        )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                "{}{}".format(self._base_url, self._path),
                json=payload,
                headers={
                    "Authorization": "Bearer {}".format(self._api_key),
                    "Content-Type": "application/json",
                },
            )
            if response.status_code == 402:
                raise LLMBillingRequired(
                    "Tinker returned 402 Payment Required. The key and endpoint "
                    "are correct — the account needs credit or an active billing "
                    "method at https://tinker-console.thinkingmachines.ai"
                )
            response.raise_for_status()
            return self._parse_completion(response.json())

    async def complete_json(
        self, prompt: str, system: Optional[str] = None, max_tokens: int = 1024
    ) -> Any:
        """Completion parsed as JSON, or ``None`` if it did not come back as JSON.

        Returns None rather than raising because callers are enrichment paths:
        a model that wandered off-format should cost us one signal, not fail an
        ingest. Temperature is pinned to 0 — structured extraction wants the
        most likely parse, not variety.
        """
        raw = await self.complete(prompt, system=system, max_tokens=max_tokens, temperature=0.0)
        return _loads_lenient(raw)

    def _build_request(
        self,
        prompt: str,
        system: Optional[str],
        max_tokens: int,
        temperature: float,
        history: Optional[List[Dict[str, str]]] = None,
        reasoning_effort: Optional[str] = None,
    ) -> Dict[str, Any]:
        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        # Prior turns sit between the system prompt and the new question, which
        # is what gives the model continuity across a conversation.
        messages.extend(history or [])
        messages.append({"role": "user", "content": prompt})
        return {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "reasoning_effort": reasoning_effort or self._reasoning_effort,
            # Keeps the deliberation out of the answer body. Without this,
            # Tinker collapses thinking tokens into message.content and the
            # reasoning ends up rendered to the reader as if it were the reply.
            "extra_body": {"separate_reasoning": True},
        }

    @staticmethod
    def _parse_completion(body: Dict[str, Any]) -> "Completion":
        choices = body.get("choices") or []
        if not choices:
            return Completion(text="", reasoning=None)
        message = choices[0].get("message") or {}
        content = (message.get("content") or "").strip()
        reasoning = message.get("reasoning_content")

        # Safety net for tinker-cookbook#684. When a trace leaks into content
        # *with* its marker intact we can recover the answer; when the marker
        # has been stripped we cannot, which is why the real defence is asking
        # for no reasoning at all rather than cleaning up afterwards.
        answer, leaked = _split_leaked_reasoning(content)
        if leaked:
            logger.warning(
                "reasoning leaked into message.content (%d chars stripped); "
                "set reasoning_effort='none' for user-facing calls",
                len(leaked),
            )
            reasoning = reasoning or leaked
        return Completion(text=answer, reasoning=reasoning)


def build_client(
    provider: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout_seconds: float = 60.0,
    reasoning_effort: str = "medium",
) -> LLMClient:
    """Construct the configured client, falling back to a no-op.

    An unconfigured or unknown provider yields ``NullClient`` and a warning
    rather than an exception, so a deployment without an API key starts
    normally and simply omits the LLM-powered features.
    """
    if provider and provider.lower() == "tinker" and api_key:
        return TinkerClient(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            reasoning_effort=reasoning_effort,
        )

    if provider and provider.lower() != "none":
        logger.warning(
            "LLM provider '%s' unavailable (missing API key?); "
            "LLM-powered features will be disabled",
            provider,
        )
    return NullClient()


#: Markers different model families use to close a reasoning block. The answer
#: is whatever follows the last one.
_REASONING_MARKERS = ("</think>", "assistantfinal", "<|start|>assistant")


def _split_leaked_reasoning(content: str):
    """Separate a leaked reasoning trace from the answer.

    Returns ``(answer, leaked_or_None)``. Only handles the case where a marker
    survived; unmarked traces are indistinguishable from prose and are left
    alone rather than guessed at, since truncating a genuine answer would be
    worse than showing a verbose one.
    """
    for marker in _REASONING_MARKERS:
        index = content.rfind(marker)
        if index != -1:
            return content[index + len(marker):].strip(), content[:index].strip()
    return content, None


def _loads_lenient(raw: str) -> Any:
    """Parse JSON, tolerating the markdown fence models habitually add."""
    text = (raw or "").strip()
    if not text:
        return None

    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except (ValueError, TypeError):
        logger.warning("LLM response was not valid JSON: %.120s", text)
        return None
