"""Registration, login, and the caller's own profile.

Both failure paths return the same message and status. "No such account" and
"wrong password" are the same 401 with identical wording, because distinguishing
them turns login into a free account-enumeration service — an attacker learns
which addresses are registered without ever guessing a password.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import Settings, get_settings
from app.dependencies import current_user, get_mailer, get_resets, get_users
from app.models.schemas import (
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterRequest,
    TokenResponse,
    UserProfile,
)
from app.services.security import AuthConfigurationError, issue_token
from app.services import rate_limit as rl
from app.services.password_reset import Mailer, ResetStore
from app.services.users import EmailAlreadyRegistered, UserStore, normalise

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
    http: Request,
    settings: Settings = Depends(get_settings),
    users: UserStore = Depends(get_users),
) -> TokenResponse:
    key = "register:" + rl.client_address(http, settings.trust_forwarded_for)
    rl.limiter.check({key: rl.REGISTER_PER_ADDRESS})
    rl.limiter.hit(key)
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
    http: Request,
    settings: Settings = Depends(get_settings),
    users: UserStore = Depends(get_users),
) -> TokenResponse:
    """Rate limited per address and per email, checked before the password.

    Checking first means a locked-out email answers 429 whether or not the
    password is right, so the limit cannot be used to confirm a guess. And
    known and unknown emails are counted alike, so it cannot reveal which
    addresses have accounts either.
    """
    address_key = "login:" + rl.client_address(http, settings.trust_forwarded_for)
    email_key = "login-fail:" + normalise(request.email)
    rl.limiter.check({
        address_key: rl.LOGIN_PER_ADDRESS,
        email_key: rl.LOGIN_FAILURES_PER_EMAIL,
    })
    rl.limiter.hit(address_key)

    user = await users.authenticate(request.email, request.password)
    if user is None:
        rl.limiter.hit(email_key)
        raise HTTPException(status_code=401, detail=CREDENTIALS_REJECTED)
    rl.limiter.reset(email_key)
    return _token_for(user, settings)


#: Identical whether or not the address has an account.
RESET_ACCEPTED = {
    "detail": "If that address has an account, a reset link is on its way. "
              "It works once, for 30 minutes."
}


@router.post("/password-reset/request", status_code=202)
async def request_password_reset(
    request: PasswordResetRequest,
    http: Request,
    settings: Settings = Depends(get_settings),
    users: UserStore = Depends(get_users),
    resets: ResetStore = Depends(get_resets),
    mailer: Mailer = Depends(get_mailer),
):
    address_key = "reset:" + rl.client_address(http, settings.trust_forwarded_for)
    email_key = "reset-email:" + normalise(request.email)
    rl.limiter.check({address_key: rl.RESET_PER_ADDRESS, email_key: rl.RESET_PER_EMAIL})
    rl.limiter.hit(address_key)
    rl.limiter.hit(email_key)

    user = await users.find(request.email)
    if user is not None:
        token = await resets.issue(user["_id"])
        link = "{}/reset-password?token={}".format(settings.public_base_url.rstrip("/"), token)
        await mailer.send_reset(user["email"], link)
    return RESET_ACCEPTED


@router.post("/password-reset/confirm", response_model=TokenResponse)
async def confirm_password_reset(
    request: PasswordResetConfirm,
    settings: Settings = Depends(get_settings),
    users: UserStore = Depends(get_users),
    resets: ResetStore = Depends(get_resets),
) -> TokenResponse:
    """Set the new password and sign in. Older login tokens stop working."""
    user_id = await resets.redeem(request.token)
    user = await users.by_id(user_id) if user_id else None
    if user is None:
        raise HTTPException(
            status_code=400,
            detail="this reset link has expired or was already used; ask for a new one",
        )
    await users.set_password(user["_id"], request.password)
    rl.limiter.reset("login-fail:" + user["email"])
    logger.info("password reset for %s", user["email"])
    return _token_for(user, settings)


@router.get("/me", response_model=UserProfile)
async def me(user=Depends(current_user)) -> UserProfile:
    return UserProfile(
        user_id=user["_id"], email=user["email"], created_at=user["created_at"]
    )
