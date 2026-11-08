"""The live race through core-api: an event stream to the browser, and the
owner-only switch for the feed."""

import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.routers import dashboard
from app.services.downstream import DownstreamUnavailable

from tests.test_admin_f1tv import OWNER, VISITOR, _client


def _events(text):
    out = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n")
                      if ": " in line and not line.startswith(":"))
        if "event" in fields:
            out.append((fields["event"], fields.get("id"), json.loads(fields["data"])))
    return out


def _stream_app(monkeypatch, answers):
    calls = []

    class Prediction:
        async def get(self, path, params=None):
            calls.append(params["since"])
            answer = answers.pop(0) if answers else {"live": True, "frames": []}
            if answer is None:
                raise DownstreamUnavailable("prediction", "connection refused")
            return json.loads(json.dumps(answer))

    monkeypatch.setattr(dashboard, "_clients", lambda settings: {"prediction": Prediction()})
    monkeypatch.setattr(dashboard, "LIVE_STREAM_POLL", 0)
    monkeypatch.setattr(dashboard, "LIVE_STREAM_MAX", 0.2)
    app = FastAPI()
    app.include_router(dashboard.router)
    app.dependency_overrides[get_settings] = lambda: Settings(jwt_secret="x" * 32)
    return TestClient(app), calls


def _now(frames, lap):
    return {"live": True, "season": 2026, "round": 22, "lap": lap, "frames": frames}


def test_the_stream_sends_the_race_then_each_new_lap_once(monkeypatch):
    client, calls = _stream_app(monkeypatch, [
        _now([{"lap": 0, "cars": []}, {"lap": 1, "cars": []}], 1),
        _now([], 1),
        _now([{"lap": 2, "cars": []}], 2),
    ])
    events = _events(client.get("/live/stream").text)
    kinds = [(e, i) for e, i, _ in events][:5]
    assert kinds == [("race", None), ("frame", "0"), ("frame", "1"),
                     ("race", None), ("frame", "2")]
    assert "frames" not in events[0][2]
    # It asks only for laps it has not sent.
    assert calls[:3] == [-1, 1, 1]


def test_a_reconnect_resumes_after_the_last_lap(monkeypatch):
    client, calls = _stream_app(monkeypatch, [_now([{"lap": 8, "cars": []}], 8)])
    client.get("/live/stream", headers={"Last-Event-ID": "7"})
    assert calls[0] == 7


def test_prediction_down_is_an_event_not_a_broken_stream(monkeypatch):
    client, _ = _stream_app(monkeypatch, [None, _now([{"lap": 3, "cars": []}], 3)])
    events = _events(client.get("/live/stream").text)
    assert events[0][0] == "unavailable"
    assert ("frame", "3") in [(e, i) for e, i, _ in events]


def test_only_the_owner_can_start_or_stop_the_feed(monkeypatch):
    client, sent = _client(VISITOR, monkeypatch, ingestion=lambda r: httpx.Response(200, json={}))
    assert client.get("/admin/live").status_code == 403
    assert client.post("/admin/live/start", json={"season": 2026, "round": 22}).status_code == 403
    assert client.post("/admin/live/stop").status_code == 403
    assert sent == []


def test_the_owner_starts_a_replay(monkeypatch):
    def ingestion(request):
        if request.url.path == "/live/recordings":
            return httpx.Response(200, json={"recordings": ["2026-21-x.txt"]})
        if request.url.path == "/live/start":
            return httpx.Response(200, json=dict(json.loads(request.content), running=True))
        return httpx.Response(200, json={"running": False})

    client, sent = _client(OWNER, monkeypatch, ingestion=ingestion)
    status = client.get("/admin/live").json()
    assert status == {"running": False, "recordings": ["2026-21-x.txt"]}
    started = client.post("/admin/live/start", json={
        "season": 2026, "round": 21, "recording": "2026-21-x.txt", "speed": 20}).json()
    assert started["running"] and started["recording"] == "2026-21-x.txt"
    assert [r.url.path for r in sent] == ["/live/status", "/live/recordings", "/live/start"]


def test_with_redis_frames_come_from_the_stream(monkeypatch):
    import asyncio

    from f1_common import streams

    memory = streams.MemoryStreams()
    streams.use_client(memory)
    key = streams.frames_stream(2026, 22)
    try:
        # The tracker already added laps 0-1; lap 2 arrives while streaming.
        asyncio.run(streams.publish(key, {"lap": 0, "cars": [], "run": "r"}))
        client, calls = _stream_app(monkeypatch, [_now([{"lap": 0, "cars": []}], 0)])
        monkeypatch.setattr(dashboard, "LIVE_STREAM_REFRESH", 60)
        real_read = streams.read_after
        sent = {"done": False}

        async def read_after(stream, after="$", block_ms=0, count=200):
            if not sent["done"]:
                sent["done"] = True
                await streams.publish(key, {"lap": 1, "cars": [], "run": "r"})
                await streams.publish(key, {"lap": 2, "cars": [], "run": "r"})
            return await real_read(stream, after, block_ms, count)

        monkeypatch.setattr(streams, "read_after", read_after)
        events = _events(client.get("/live/stream").text)
    finally:
        streams.use_client(None)
    frames = [(i, d) for e, i, d in events if e == "frame"]
    assert [i for i, _ in frames] == ["0", "1", "2"]
    assert all("run" not in d for _, d in frames)
    # Prediction-service asked once for the race; the laps came from Redis.
    assert calls == [-1]
