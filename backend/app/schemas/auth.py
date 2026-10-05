"""Auth request/response schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.models.enums import UserRole
from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    # Deliberately a plain string: on login the address is only a lookup key, and
    # rejecting it on format would leak a 422 where a uniform 401 belongs.
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=72)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    organization_id: uuid.UUID


class TokenPayload(BaseModel):
    sub: str | None = None
    exp: int | None = None


# Backward-compatible aliases
Token = TokenResponse
UserLogin = LoginRequest
