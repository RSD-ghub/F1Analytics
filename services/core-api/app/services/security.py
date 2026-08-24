"""Password hashing and JWT issuing.

**The secret has no default.** ``Settings.jwt_secret`` is an empty string until
the environment supplies one, and ``require_secret`` refuses to start the auth
path without it. A development fallback like ``"changeme"`` is worse than no
key at all: it works, so nobody notices, and it ships.

Tokens are stateless and short-lived rather than revocable. That is the right
trade at this size — a revocation list is a database read on every request, and
there is nothing yet that needs immediate revocation. It is also the thing to
revisit first if that changes.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import bcrypt
import jwt

logger = logging.getLogger(__name__)


class AuthConfigurationError(RuntimeError):
    """The service is not configured to issue tokens safely."""


class InvalidToken(RuntimeError):
    """Token is absent, malformed, expired, or signed with the wrong key."""


#: RFC 7518 §3.2 requires an HMAC key at least as long as the hash output —
#: 32 bytes for HS256. PyJWT warns below this rather than refusing, and a
#: warning in a log nobody reads is not a control. A short secret is only
#: marginally better than a default one: both are brute-forceable offline, and
#: forging a token means forging any user.
MIN_SECRET_BYTES = 32


def require_secret(secret: str) -> str:
    if not secret or not secret.strip():
        raise AuthConfigurationError(
            "JWT_SECRET is not set. core-api will not issue tokens signed with "
            "a default key — refusing to start the auth path rather than "
            "shipping a guessable one."
        )
    if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise AuthConfigurationError(
            "JWT_SECRET is {} bytes; HS256 needs at least {} (RFC 7518 §3.2). "
            "Generate one with: python -c \"import secrets; "
            "print(secrets.token_urlsafe(48))\"".format(
                len(secret.encode("utf-8")), MIN_SECRET_BYTES
            )
        )
    return secret


def hash_password(password: str) -> str:
    """bcrypt with a per-password salt, generated for us.

    bcrypt rather than a bare SHA: it is deliberately slow and salted, which is
    what makes a stolen hash table expensive rather than instant to reverse.
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Constant-time comparison, and never raises on a malformed hash.

    A corrupt stored hash must read as "wrong password", not as a 500 — the
    error response itself would tell an attacker that the account exists.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        logger.warning("stored password hash is unreadable; treating as a mismatch")
        return False


def issue_token(
    user_id: str, email: str, secret: str, algorithm: str, expiry_hours: int
) -> Tuple[str, datetime]:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
    payload = {
        "sub": user_id,
        "email": email,
        "exp": expires_at,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, require_secret(secret), algorithm=algorithm)
    return token, expires_at


def decode_token(token: str, secret: str, algorithm: str) -> Dict[str, Any]:
    """Verify and decode. Every failure mode collapses to ``InvalidToken``.

    Distinguishing "expired" from "bad signature" in the response would leak
    whether a token was ever valid, which is information an attacker can use.
    """
    try:
        return jwt.decode(token, require_secret(secret), algorithms=[algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise InvalidToken("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidToken("token is not valid") from exc


def bearer_token(header: Optional[str]) -> str:
    """Pull the credential out of an Authorization header."""
    if not header:
        raise InvalidToken("missing Authorization header")
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise InvalidToken("Authorization header must be 'Bearer <token>'")
    return parts[1].strip()
