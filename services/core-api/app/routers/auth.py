"""Registration, login, and the caller's own profile.

Both failure paths return the same message and status. "No such account" and
"wrong password" are the same 401 with identical wording, because distinguishing
them turns login into a free account-enumeration service — an attacker learns
which addresses are registered without ever guessing a password.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings, get_settings
from app.dependencies import current_user, get_users
from app.models.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserProfile,
)
from app.services.security import AuthConfigurationError, issue_token
from app.services.users import EmailAlreadyRegistered, UserStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

CREDENTIALS_REJECTED = "email or password is incorrect"


def _token_for(user, settings: Settings) -> TokenResponse:
    try:
        token, expires_at = issue_token(
            user_id=user["_id"],
            email=user["email"],
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
            expiry_hours=settings.jwt_expiry_hours,
        )
    except AuthConfigurationError as exc:
        # Our misconfiguration, not the caller's problem.
        logger.error("cannot issue token: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    request: RegisterRequest,
    settings: Settings = Depends(get_settings),
    users: UserStore = Depends(get_users),
) -> TokenResponse:
    try:
        user = await users.create(request.email, request.password)
    except EmailAlreadyRegistered as exc:
        # 409 here is a deliberate exception to the no-enumeration rule:
        # registration cannot usefully hide that an address is taken, since the
        # user must be told why their signup failed. Login stays opaque.
        raise HTTPException(status_code=409, detail=str(exc))
    logger.info("registered %s", user["email"])
    return _token_for(user, settings)


@router.post("/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    settings: Settings = Depends(get_settings),
    users: UserStore = Depends(get_users),
) -> TokenResponse:
    user = await users.authenticate(request.email, request.password)
    if user is None:
        raise HTTPException(status_code=401, detail=CREDENTIALS_REJECTED)
    return _token_for(user, settings)


@router.get("/me", response_model=UserProfile)
async def me(user=Depends(current_user)) -> UserProfile:
    return UserProfile(
        user_id=user["_id"], email=user["email"], created_at=user["created_at"]
    )
