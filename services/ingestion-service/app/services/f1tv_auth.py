"""Signing FastF1 in to F1TV, in one place.

Since October 2026 F1's live-timing archive answers only requests that carry
an F1TV Access (or higher) subscription token. FastF1 3.9 knows how to get
one; this module is the single place the service touches that machinery, so
ingestion and the live client share one token and one set of rules.

The rules:

* **The password is never stored.** ``connect`` signs in once and FastF1
  keeps only the resulting token (in its user-data folder, mode 0600). F1
  issues no refresh token, so when it expires (roughly every four days) the
  owner connects again from the Data health page.
* **A server never prompts.** FastF1's default sign-in asks on the terminal
  when it has no credentials and stdin is a TTY. A service started from a
  terminal would then hang mid-request waiting for a password nobody sees,
  so the default is replaced with one that only uses ``F1_USERNAME`` and
  ``F1_PASSWORD`` if the owner chose to set them, and otherwise fails fast.
  FastF1 then carries on without a token and the OpenF1 fallback takes over.
* **Nothing here logs a credential.** Errors are FastF1's own messages,
  which name the problem ("wrong password", "no subscription") without it.

Everything is imported lazily: FastF1 older than 3.9 (Python 3.9) has no
F1TV support, and the service must still start there.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

ENV_USERNAME = "F1_USERNAME"
ENV_PASSWORD = "F1_PASSWORD"

_lock = threading.Lock()
_installed = False


class F1TVUnavailable(RuntimeError):
    """This FastF1 cannot sign in to F1TV at all (older than 3.9)."""


class F1TVSignInFailed(RuntimeError):
    """F1 refused the sign-in: wrong details, or no qualifying subscription."""


def _account():
    try:
        from fastf1.internals import f1account
    except ImportError as exc:
        raise F1TVUnavailable(
            "FastF1 3.9 or newer is needed for F1TV (Python 3.10+)"
        ) from exc
    return f1account


def available() -> bool:
    try:
        _account()
    except F1TVUnavailable:
        return False
    return True


def _env_credentials() -> Optional[tuple]:
    username = os.environ.get(ENV_USERNAME)
    password = os.environ.get(ENV_PASSWORD)
    return (username, password) if username and password else None


def install_no_prompt() -> bool:
    """Make FastF1's automatic sign-in non-interactive. Safe to call twice."""
    global _installed
    if _installed:
        return True
    try:
        account = _account()
    except F1TVUnavailable:
        return False
    from fastf1.exceptions import F1AuthError
    from fastf1.internals.f1account.login import login

    def _sign_in_without_prompt():
        credentials = _env_credentials()
        if credentials is None:
            raise F1AuthError(
                "F1TV is not connected; connect it from the Data health page"
            )
        return login(*credentials)

    manager = account.token_manager
    if hasattr(manager, "_default_auth_func"):
        manager._default_auth_func = _sign_in_without_prompt
    else:
        logger.warning("FastF1's token manager changed shape; F1TV may prompt")
        return False
    _installed = True
    return True


def status() -> Dict[str, Any]:
    """What the Data health page shows. Never includes the token itself."""
    out: Dict[str, Any] = {
        "available": available(),
        "connected": False,
        "expires_at": None,
        "hours_left": None,
        "auto_renew": _env_credentials() is not None,
    }
    if not out["available"]:
        out["detail"] = "FastF1 3.9 or newer is needed (Python 3.10+)"
        return out
    token = _account().token_manager.get_stored()
    if token is None:
        out["detail"] = "not connected"
        return out
    out["expires_at"] = datetime.fromtimestamp(
        token.expires_at, tz=timezone.utc
    ).isoformat()
    out["hours_left"] = round((token.expires_at - time.time()) / 3600, 1)
    out["connected"] = token.is_valid()
    out["detail"] = "connected" if out["connected"] else "token expired; connect again"
    return out


def connect(username: str, password: str) -> Dict[str, Any]:
    """Sign in once and keep the token. The password goes no further."""
    account = _account()
    from fastf1.exceptions import F1AuthError
    from fastf1.internals.f1account.login import login

    install_no_prompt()
    with _lock:
        try:
            token = login(username, password)
        except F1AuthError as exc:
            raise F1TVSignInFailed(str(exc)) from None
        account.token_manager.store(token)
        # The manager keeps an in-memory copy too; drop any stale one so the
        # next request reads the token just stored.
        if hasattr(account.token_manager, "_token"):
            account.token_manager._token = token
    logger.info("F1TV connected; token valid until %s",
                datetime.fromtimestamp(token.expires_at, tz=timezone.utc).isoformat())
    return status()


def disconnect() -> Dict[str, Any]:
    account = _account()
    with _lock:
        account.clear_token()
    logger.info("F1TV disconnected")
    return status()
