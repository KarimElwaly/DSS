"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.security import create_access_token, verify_password
from app.models.enums import AuditAction
from app.models.organization import User
from app.schemas.auth import LoginRequest, TokenResponse, UserOut
from app.services.audit import record_event

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, session: DbSession) -> TokenResponse:
    settings = get_settings()
    user = session.scalars(select(User).where(User.email == payload.email)).first()

    # Uniform failure response: never reveal whether the email exists.
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password."
        )

    record_event(
        session,
        organization_id=user.organization_id,
        action=AuditAction.LOGIN,
        entity_type="app_user",
        entity_id=user.id,
        summary=f"{user.email} signed in",
        actor_user_id=user.id,
        actor_label=user.email,
    )
    session.commit()

    token = create_access_token(user.id, organization_id=user.organization_id, role=user.role.value)
    return TokenResponse(access_token=token, expires_in=settings.access_token_ttl_minutes * 60)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user
