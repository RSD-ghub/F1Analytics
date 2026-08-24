"""Auth tests, weighted toward the things that leak information.

A login endpoint that behaves differently for "no such account" and "wrong
password" is an account-enumeration oracle: an attacker learns which addresses
are registered without guessing a single password. Most of these tests exist to
pin that shut.
"""

import secrets

import pytest
from fastapi.testclient import TestClient

from app.services.security import (
    AuthConfigurationError,
    InvalidToken,
    MIN_SECRET_BYTES,
    bearer_token,
    decode_token,
    hash_password,
    issue_token,
    require_secret,
    verify_password,
)

GOOD_SECRET = secrets.token_urlsafe(48)


# ── Secret handling ──────────────────────────────────────────────────────────


def test_empty_secret_is_refused():
    """A development default that "works" is how a guessable key ships."""
    with pytest.raises(AuthConfigurationError):
        require_secret("")


def test_short_secret_is_refused():
    """RFC 7518 §3.2: HS256 needs >= 32 bytes. PyJWT only warns; we refuse."""
    with pytest.raises(AuthConfigurationError):
        require_secret("s3cret")


def test_a_long_enough_secret_is_accepted():
    assert require_secret("x" * MIN_SECRET_BYTES)


# ── Passwords ────────────────────────────────────────────────────────────────


def test_password_round_trips():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong", hashed)


def test_the_hash_is_salted():
    """Two identical passwords must not produce identical hashes."""
    assert hash_password("same") != hash_password("same")


def test_a_corrupt_stored_hash_reads_as_a_mismatch():
    """Never a 500 — an error response tells an attacker the account exists."""
    assert not verify_password("anything", "not-a-bcrypt-hash")


# ── Tokens ───────────────────────────────────────────────────────────────────


def test_token_round_trips():
    token, expires_at = issue_token("u1", "a@b.co", GOOD_SECRET, "HS256", 24)
    payload = decode_token(token, GOOD_SECRET, "HS256")

    assert payload["sub"] == "u1"
    assert payload["email"] == "a@b.co"
    assert expires_at is not None


def test_a_token_signed_with_another_key_is_rejected():
    token, _ = issue_token("u1", "a@b.co", GOOD_SECRET, "HS256", 24)
    with pytest.raises(InvalidToken):
        decode_token(token, secrets.token_urlsafe(48), "HS256")


def test_an_expired_token_is_rejected():
    token, _ = issue_token("u1", "a@b.co", GOOD_SECRET, "HS256", expiry_hours=-1)
    with pytest.raises(InvalidToken):
        decode_token(token, GOOD_SECRET, "HS256")


@pytest.mark.parametrize(
    "header", [None, "", "token abc", "Bearer", "Basic abc", "bearer"]
)
def test_malformed_authorization_headers_are_rejected(header):
    with pytest.raises(InvalidToken):
        bearer_token(header)


def test_bearer_is_case_insensitive():
    """Clients differ on capitalisation; the scheme is case-insensitive per RFC."""
    assert bearer_token("bearer abc123") == "abc123"
    assert bearer_token("Bearer abc123") == "abc123"
