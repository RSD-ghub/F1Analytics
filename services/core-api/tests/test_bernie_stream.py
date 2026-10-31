"""Bernie answers streamed as server-sent events."""

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.models.conversation import Role, Thread, Turn
from f1_common import llm as llm_module
from f1_common.llm import LLMBillingRequired, TinkerClient

BASE = "https://tinker.example/api/v1"


def _sse_body(*fragments, done=True):
    lines = []
    for text in fragments:
        lines.append("data: " + json.dumps({"choices": [{"delta": {"content": text}}]}))
    lines.append("data: " + json.dumps({"choices": [{"delta": {"reasoning_content": "hidden"}}]}))
    lines.append(": keep-alive comment")
    if done:
        lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode()


@pytest.fixture
def transport(monkeypatch):
    class _Transport(httpx.AsyncBaseTransport):
        status = 200
        body = b""
        requests = []

        async def handle_async_request(self, request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(self.status, content=self.body)

    holder = _Transport()
    real = httpx.AsyncClient
    monkeypatch.setattr(
        llm_module.httpx, "AsyncClient", lambda **kw: real(transport=holder, **kw)
    )
    return holder


async def _collect(client, **kwargs):
    return [part async for part in client.stream("q", **kwargs)]


async def test_the_client_yields_content_deltas_only(transport):
    transport.body = _sse_body("One ", "stop, ", "lap 22.")
    parts = await _collect(TinkerClient("key", BASE, "m"))

    assert parts == ["One ", "stop, ", "lap 22."]
    assert transport.requests[0]["stream"] is True


async def test_out_of_credit_is_still_recognisable_when_streaming(transport):
    transport.status = 402
    with pytest.raises(LLMBillingRequired):
        await _collect(TinkerClient("key", BASE, "m"))


# ── The route ────────────────────────────────────────────────────────────────


class _LLM:
    available = True

    def __init__(self, fragments=None, fail=None):
        self.fragments, self.fail = fragments or [], fail

    async def stream(self, **kwargs):
        for fragment in self.fragments:
            yield fragment
        if self.fail:
            raise self.fail


class _Store:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.thread = Thread(thread_id="t1", user_id="u1", season=2026, round=20,
                             created_at=now, updated_at=now)

    async def create(self, user_id, season, round_number):
        return self.thread

    async def get(self, thread_id, user_id):
        return self.thread

    async def append(self, thread, role, content, reasoning=None):
        thread.turns.append(Turn(role=role, content=content,
                                 created_at=datetime.now(timezone.utc), reasoning=reasoning))
        return thread


class _Usage:
    async def consume(self, user_id, per_user, per_day):
        return None


def _events(body: str):
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture
def app_with(monkeypatch):
    from app import dependencies
    from app.main import app
    from app.routers import bernie as router

    async def no_facts(*args, **kwargs):
        return {}

    for name in ("_weekend_facts", "_regulation_facts", "_news_facts",
                 "_guide_facts", "_strategy_facts"):
        monkeypatch.setattr(router, name, no_facts)

    def configure(llm):
        store = _Store()
        app.dependency_overrides[dependencies.get_llm] = lambda: llm
        app.dependency_overrides[dependencies.get_conversations] = lambda: store
        app.dependency_overrides[dependencies.get_usage] = lambda: _Usage()
        app.dependency_overrides[dependencies.current_user] = lambda: {"_id": "u1"}
        return TestClient(app), store

    yield configure
    app.dependency_overrides.clear()


def test_a_streamed_turn_sends_fragments_then_the_saved_answer(app_with):
    client, store = app_with(_LLM(["Box ", "on lap 22."]))
    response = client.post("/bernie/threads?stream=true",
                           json={"season": 2026, "round": 20, "question": "When to pit?"})

    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert [e for e, _ in events] == ["delta", "delta", "done"]
    assert events[-1][1]["answer"] == "Box on lap 22."
    assert [t.role for t in store.thread.turns] == [Role.USER, Role.ASSISTANT]
    assert store.thread.turns[-1].content == "Box on lap 22."


def test_a_provider_failure_mid_stream_ends_with_an_error_event(app_with):
    client, store = app_with(_LLM(["Box "], fail=RuntimeError("upstream reset")))
    response = client.post("/bernie/threads/t1?stream=true", json={"question": "And then?"})

    events = _events(response.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["reason"] == "provider_error"
    # The question is kept; no half answer is saved.
    assert [t.role for t in store.thread.turns] == [Role.USER]


def test_without_the_flag_the_route_still_returns_json(app_with):
    class _Plain:
        available = True

        async def complete(self, **kwargs):
            return "One stop."

    client, _ = app_with(_Plain())
    body = client.post("/bernie/threads/t1", json={"question": "Stops?"}).json()
    assert body["answer"] == "One stop."
