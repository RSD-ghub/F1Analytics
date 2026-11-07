"""Login rate limits and password reset.

Both are written against the enumeration rule the rest of auth keeps: no
response may differ between an address that has an account and one that
does not.
"""

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.dependencies import current_user, get_mailer, get_resets, get_users
from app.routers import auth
from app.services import rate_limit as rl
from app.services.password_reset import ResetStore, TOKEN_LIFETIME
from app.services.security import hash_password, issue_token, verify_password

SECRET = secrets.token_urlsafe(48)
PASSWORD = "correct horse battery"


# ── Fakes ────────────────────────────────────────────────────────────────────


class _Collection:
    def __init__(self):
        self.rows = []

    def _match(self, row, query):
        return all(row.get(k) == v for k, v in query.items())

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def find_one_and_update(self, query, update):
        for row in self.rows:
            if self._match(row, query):
                before = dict(row)
                row.update(update["$set"])
                return before
        return None

    async def update_many(self, query, update):
        for row in self.rows:
            if self._match(row, query):
                row.update(update["$set"])


class _DB(dict):
    def __missing__(self, key):
        self[key] = _Collection()
        return self[key]


class _Users:
    def __init__(self):
        self.by_email = {}

    def add(self, email, password=PASSWORD):
        user = {"_id": "u-" + email, "email": email, "password_hash": hash_password(password),
                "created_at": datetime.now(timezone.utc)}
        self.by_email[email] = user
        return user

    async def find(self, email):
        return self.by_email.get(email.strip().lower())

    async def by_id(self, user_id):
        return next((u for u in self.by_email.values() if u["_id"] == user_id), None)

    async def authenticate(self, email, password):
        user = await self.find(email)
        if user and verify_password(password, user["password_hash"]):
            return user
        return None

    async def set_password(self, user_id, password):
        user = await self.by_id(user_id)
        user["password_hash"] = hash_password(password)
        user["password_changed_at"] = datetime.now(timezone.utc)


class _Mailer:
    def __init__(self):
        self.sent = []

    async def send_reset(self, to, link):
        self.sent.append((to, link))


@pytest.fixture
def world():
    rl.limiter = rl.RateLimiter()
    users, resets, mailer = _Users(), ResetStore(_DB()), _Mailer()
    users.add("deepu@example.com")
    settings = Settings(jwt_secret=SECRET, public_base_url="https://f1.test")
    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides.update({
        get_users: lambda: users, get_resets: lambda: resets,
        get_mailer: lambda: mailer, get_settings: lambda: settings,
    })
    return TestClient(app), users, resets, mailer


def _login(client, email, password):
    return client.post("/auth/login", json={"email": email, "password": password})


# ── Rate limits ──────────────────────────────────────────────────────────────


def test_the_limiter_window_slides():
    now = [0.0]
    limiter = rl.RateLimiter(clock=lambda: now[0])
    limit = rl.Limit(attempts=2, window_seconds=60)
    limiter.hit("k"); limiter.hit("k")
    assert limiter.retry_after("k", limit) == 60
    now[0] = 61
    assert limiter.retry_after("k", limit) is None


def test_five_failures_lock_an_email_even_with_the_right_password(world):
    client, *_ = world
    for _ in range(5):
        assert _login(client, "deepu@example.com", "wrong password").status_code == 401
    locked = _login(client, "deepu@example.com", PASSWORD)
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0


def test_an_unknown_email_locks_exactly_like_a_known_one(world):
    client, *_ = world
    known = [_login(client, "deepu@example.com", "nope nope nope").status_code for _ in range(6)]
    unknown = [_login(client, "nobody@example.com", "nope nope nope").status_code for _ in range(6)]
    assert known == unknown == [401] * 5 + [429]


def test_a_success_clears_the_failure_count(world):
    client, *_ = world
    for _ in range(4):
        _login(client, "deepu@example.com", "wrong password")
    assert _login(client, "deepu@example.com", PASSWORD).status_code == 200
    for _ in range(4):
        assert _login(client, "deepu@example.com", "wrong password").status_code == 401


# ── Password reset ───────────────────────────────────────────────────────────


def _request_reset(client, email):
    return client.post("/auth/password-reset/request", json={"email": email})


def test_the_request_answers_the_same_for_unknown_addresses(world):
    client, _, _, mailer = world
    known = _request_reset(client, "deepu@example.com")
    unknown = _request_reset(client, "nobody@example.com")
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json())
    assert [to for to, _ in mailer.sent] == ["deepu@example.com"]
    assert mailer.sent[0][1].startswith("https://f1.test/reset-password?token=")


def test_the_token_is_stored_only_as_a_hash(world):
    client, _, resets, mailer = world
    _request_reset(client, "deepu@example.com")
    token = mailer.sent[0][1].split("token=")[1]
    stored = resets._db["password_resets"].rows[0]
    assert token not in str(stored)


def test_a_reset_works_once_and_signs_in(world):
    client, _, _, mailer = world
    _request_reset(client, "deepu@example.com")
    token = mailer.sent[0][1].split("token=")[1]

    done = client.post("/auth/password-reset/confirm", json={"token": token, "password": "a new long password"})
    assert done.status_code == 200 and done.json()["access_token"]
    assert _login(client, "deepu@example.com", "a new long password").status_code == 200
    assert _login(client, "deepu@example.com", PASSWORD).status_code == 401

    again = client.post("/auth/password-reset/confirm", json={"token": token, "password": "another long password"})
    assert again.status_code == 400


def test_an_expired_token_is_refused(world):
    client, users, resets, _ = world

    async def issue_old():
        return await resets.issue("u-deepu@example.com",
                                  now=datetime.now(timezone.utc) - TOKEN_LIFETIME - timedelta(minutes=1))
    import asyncio
    token = asyncio.run(issue_old())
    response = client.post("/auth/password-reset/confirm", json={"token": token, "password": "a new long password"})
    assert response.status_code == 400


def test_the_new_password_obeys_the_length_rule(world):
    client, *_ = world
    response = client.post("/auth/password-reset/confirm", json={"token": "x" * 40, "password": "short"})
    assert response.status_code == 422


async def test_tokens_issued_before_a_reset_stop_working(world):
    _, users, _, _ = world
    user = users.by_email["deepu@example.com"]
    old, _ = issue_token(user["_id"], user["email"], SECRET, "HS256", 24)
    await users.set_password(user["_id"], "a new long password")
    user["password_changed_at"] += timedelta(seconds=5)  # make the ordering unambiguous

    settings = Settings(jwt_secret=SECRET)
    with pytest.raises(Exception) as caught:
        await current_user(f"Bearer {old}", settings, users)
    assert getattr(caught.value, "status_code", None) == 401
