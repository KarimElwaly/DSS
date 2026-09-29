"""Password hashing and JWT helpers."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    # bcrypt silently truncates at 72 bytes; reject rather than hash a prefix.
    encoded = password.encode("utf-8")
    if len(encoded) > 72:
        raise ValueError("Password must be at most 72 bytes.")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(
    subject: uuid.UUID, *, organization_id: uuid.UUID, role: str, expires_minutes: int | None = None
) -> str:
    settings = get_settings()
    ttl = expires_minutes or settings.access_token_ttl_minutes
    now = dt.datetime.now(dt.UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "org": str(organization_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(minutes=ttl)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
