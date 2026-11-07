"""The F1TV sign-in helper.

Two properties matter most: a running service never stops to ask for a
password on a terminal, and connecting keeps the token but never the
password.
"""

import time

import pytest

fastf1_account = pytest.importorskip("fastf1.internals.f1account")

from fastf1.exceptions import F1AuthError  # noqa: E402
from fastf1.internals.f1account import login as login_module  # noqa: E402
from fastf1.internals.f1account.tokens import SubscriptionToken, TokenManager  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import f1tv  # noqa: E402
from app.services import f1tv_auth  # noqa: E402


@pytest.fixture
def manager(tmp_path, monkeypatch):
    fresh = TokenManager(auth_func=lambda: pytest.fail("prompted"),
                         path=tmp_path / "token.json")
    monkeypatch.setattr(fastf1_account, "token_manager", fresh)
    monkeypatch.setattr(f1tv_auth, "_installed", False)
    monkeypatch.delenv("F1_USERNAME", raising=False)
    monkeypatch.delenv("F1_PASSWORD", raising=False)
    return fresh


def _token(hours=96):
    return SubscriptionToken(value="header.payload.sig", expires_at=time.time() + hours * 3600)


def test_without_credentials_the_service_fails_fast_instead_of_prompting(manager, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert f1tv_auth.install_no_prompt()
    with pytest.raises(F1AuthError):
        manager.get()


def test_connect_keeps_the_token_and_not_the_password(manager, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(login_module, "login", lambda u, p: seen.append((u, p)) or _token())

    out = f1tv_auth.connect("me@f1.tv", "s3cret-pass")

    assert seen == [("me@f1.tv", "s3cret-pass")]
    assert out["connected"] is True and 95 < out["hours_left"] <= 96
    stored = (tmp_path / "token.json").read_text()
    assert "s3cret-pass" not in stored and "me@f1.tv" not in stored


def test_a_refused_sign_in_is_reported_as_such(manager, monkeypatch):
    def refuse(u, p):
        raise F1AuthError("An active F1TV subscription is required.")

    monkeypatch.setattr(login_module, "login", refuse)
    app = FastAPI()
    app.include_router(f1tv.router)
    out = TestClient(app).post("/f1tv/connect", json={"username": "me@f1.tv", "password": "x"})
    assert out.status_code == 401
    assert "subscription" in out.json()["detail"]
    assert f1tv_auth.status()["connected"] is False


def test_an_expired_token_shows_as_not_connected(manager):
    manager.store(_token(hours=-1))
    out = f1tv_auth.status()
    assert out["connected"] is False and out["expires_at"] is not None


def test_disconnect_removes_the_token(manager, monkeypatch):
    monkeypatch.setattr(login_module, "login", lambda u, p: _token())
    f1tv_auth.connect("me@f1.tv", "pw")
    assert f1tv_auth.disconnect()["connected"] is False
    assert manager.get_stored() is None


def test_credentials_never_appear_in_their_repr():
    shown = repr(f1tv.Credentials(username="me@f1.tv", password="s3cret-pass"))
    assert "s3cret-pass" not in shown
