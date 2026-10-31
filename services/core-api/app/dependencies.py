"""FastAPI dependency wiring for core-api."""

from datetime import timezone
from functools import lru_cache
from typing import Optional

from fastapi import Depends, Header, HTTPException

from app import db
from app.config import Settings, get_settings
from app.services.security import (
    AuthConfigurationError,
    InvalidToken,
    bearer_token,
    decode_token,
)
from app.services.conversation import ConversationStore
from app.services.password_reset import Mailer, ResetStore
from app.services.usage import UsageStore
from app.services.users import UserStore
from f1_common.llm import LLMClient, build_client


def get_users() -> UserStore:
    return UserStore(db.db())


def get_resets() -> ResetStore:
    return ResetStore(db.db())


def get_mailer() -> Mailer:
    s = get_settings()
    return Mailer(s.smtp_host, s.smtp_port, s.smtp_username, s.smtp_password,
                  s.smtp_sender, s.smtp_starttls)


def get_conversations() -> ConversationStore:
    return ConversationStore(db.db())


def get_usage() -> UsageStore:
    return UsageStore(db.db())


@lru_cache
def get_llm() -> LLMClient:
    """One client per process; a no-op client when unconfigured.

    Bernie and the explanation layer degrade to silence without a key rather
    than failing the request — they are commentary on a forecast, not the
    forecast itself.
    """
    settings: Settings = get_settings()
    return build_client(
        provider=settings.llm_provider,
        api_key=settings.tinker_api_key,
        base_url=settings.tinker_base_url,
        model=settings.inkling_model,
        reasoning_effort=settings.inkling_reasoning_effort,
    )


async def current_user(
    authorization: Optional[str] = Header(None),
    settings: Settings = Depends(get_settings),
    users: UserStore = Depends(get_users),
):
    """Resolve the caller, or 401.

    A misconfigured secret is a 500, not a 401: it is our fault, and reporting
    it as "unauthorised" would send an operator hunting for a credential bug
    that does not exist.
    """
    try:
        token = bearer_token(authorization)
        payload = decode_token(token, settings.jwt_secret, settings.jwt_algorithm)
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except InvalidToken as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await users.by_id(payload.get("sub", ""))
    if user is None:
        # A valid signature for a deleted account. The token is genuine, the
        # subject is gone.
        raise HTTPException(status_code=401, detail="account no longer exists")
    changed = user.get("password_changed_at")
    issued = payload.get("iat")
    if changed is not None and issued is not None:
        if changed.tzinfo is None:
            changed = changed.replace(tzinfo=timezone.utc)
        # A token signed before the password changed belongs to whoever held
        # the old password, which after a reset may not be the account owner.
        # One second of grace: ``iat`` is whole seconds.
        if issued + 1 < changed.timestamp():
            raise HTTPException(
                status_code=401, detail="password changed; sign in again",
                headers={"WWW-Authenticate": "Bearer"},
            )
    return user
