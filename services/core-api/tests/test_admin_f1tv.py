"""Owner-only F1TV connection.

The form is hidden from everyone but the owner, but hiding is not the
protection: every route checks ADMIN_EMAILS on the server. A refused F1
sign-in must not come back as 401, or the dashboard would read it as an
expired session and sign the owner out.
"""

from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.dependencies import current_user
from app.routers import admin, auth
from app.services import rate_limit as rl

OWNER = {"_id": "u-owner", "email": "Owner@Example.com",
         "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
VISITOR = {"_id": "u-visitor", "email": "visitor@example.com",
           "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}


def _client(user, monkeypatch, ingestion=None):
    app = FastAPI()
    app.include_router(admin.router)
    app.include_router(auth.router)
    settings = Settings(admin_emails="owner@example.com, other@example.com",
                        ingestion_service_url="http://ingestion.test")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[current_user] = lambda: user
    sent = []
    if ingestion is not None:
        def handler(request):
            sent.append(request)
            return ingestion(request)
        transport = httpx.MockTransport(handler)
        real = httpx.AsyncClient
        monkeypatch.setattr(admin.httpx, "AsyncClient",
                            lambda **kw: real(transport=transport, **kw))
    rl.limiter = rl.RateLimiter()
    return TestClient(app), sent


def test_a_visitor_cannot_see_or_change_the_connection(monkeypatch):
    client, sent = _client(VISITOR, monkeypatch, ingestion=lambda r: httpx.Response(200, json={}))
    assert client.get("/admin/f1tv").status_code == 403
    assert client.post("/admin/f1tv/connect",
                       json={"username": "a@b.c", "password": "x"}).status_code == 403
    assert client.post("/admin/f1tv/disconnect").status_code == 403
    assert sent == []
    assert client.get("/auth/me").json()["is_admin"] is False


def test_the_owner_is_recognised_whatever_the_case(monkeypatch):
    client, _ = _client(OWNER, monkeypatch)
    assert client.get("/auth/me").json()["is_admin"] is True


def test_nobody_is_an_owner_by_default():
    assert Settings().is_admin("owner@example.com") is False


def test_connect_passes_the_credentials_through_once(monkeypatch):
    client, sent = _client(
        OWNER, monkeypatch,
        ingestion=lambda r: httpx.Response(200, json={"connected": True, "hours_left": 95.0}),
    )
    out = client.post("/admin/f1tv/connect", json={"username": "me@f1.tv", "password": "pw"})
    assert out.status_code == 200 and out.json()["connected"] is True
    assert [r.url.path for r in sent] == ["/f1tv/connect"]


def test_a_refused_sign_in_is_not_reported_as_an_expired_session(monkeypatch):
    client, _ = _client(
        OWNER, monkeypatch,
        ingestion=lambda r: httpx.Response(401, json={"detail": "An active F1TV subscription is required."}),
    )
    out = client.post("/admin/f1tv/connect", json={"username": "me@f1.tv", "password": "pw"})
    assert out.status_code == 400
    assert "subscription" in out.json()["detail"]


def test_repeated_wrong_passwords_are_slowed_down(monkeypatch):
    client, _ = _client(OWNER, monkeypatch,
                        ingestion=lambda r: httpx.Response(401, json={"detail": "wrong"}))
    codes = [client.post("/admin/f1tv/connect",
                         json={"username": "me@f1.tv", "password": "pw"}).status_code
             for _ in range(6)]
    assert codes[:5] == [400] * 5 and codes[5] == 429
