"""Wire models for core-api.

This is the only publicly reachable service, so these shapes are the public
API contract. Internal service models are deliberately not re-exported: the
browser should not be coupled to prediction-service's storage layout.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

#: Short enough to be typed, long enough that an offline crack is not trivial.
MIN_PASSWORD_LENGTH = 10

#: bcrypt only reads the first 72 bytes of a password, and bcrypt 5 refuses
#: anything longer instead of quietly truncating it. Counted in UTF-8 bytes, not
#: characters: 40 accented letters are already 80 bytes.
MAX_PASSWORD_BYTES = 72


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def long_enough(cls, value: str) -> str:
        """Length only.

        Composition rules ("one symbol, one digit") measurably push people
        toward `Password1!` and its cousins. Length is the property that
        actually resists an offline attack, so it is the one enforced.
        """
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                "password must be at least {} characters".format(MIN_PASSWORD_LENGTH)
            )
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(
                "password must be at most {} bytes (plain letters count 1, "
                "accented letters 2)".format(MAX_PASSWORD_BYTES)
            )
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class UserProfile(BaseModel):
    """Never carries the password hash — this model is returned to the browser."""

    user_id: str
    email: str
    created_at: datetime


class HealthSummary(BaseModel):
    service: str
    reachable: bool
    detail: str = ""


class SystemStatus(BaseModel):
    """Aggregated view of the internal services, for the status page."""

    services: List[HealthSummary] = Field(default_factory=list)
    all_reachable: bool = True
