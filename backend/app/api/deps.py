"""FastAPI dependencies: auth, tenancy, pagination."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.db import get_db
from app.models.enums import UserRole
from app.models.organization import Organization, User
from app.schemas.common import PageParams

_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _demo_user(session: Session) -> User:
    """The seeded admin, used when the auth bypass is on.

    Resolved per request rather than cached so that re-seeding the database
    does not leave the API holding a stale, detached user object.
    """
    user = session.scalars(
        select(User)
        .where(User.email == get_settings().admin_email, User.is_active.is_(True))
        .order_by(User.created_at)
    ).first()
    if user is None:
        # The bypass picks an existing account; it never invents one. An empty
        # database must still fail closed.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth is disabled but no admin user exists. Run `dss seed` first.",
        )
    return user


def get_current_user(
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    if get_settings().auth_bypass_active:
        return _demo_user(session)
    if credentials is None:
        raise _CREDENTIALS_ERROR
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise _CREDENTIALS_ERROR from exc

    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise _CREDENTIALS_ERROR
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_org(session: DbSession, user: CurrentUser) -> Organization:
    org = session.get(Organization, user.organization_id)
    if org is None:
        raise _CREDENTIALS_ERROR
    return org


CurrentOrg = Annotated[Organization, Depends(get_current_org)]

_ROLE_RANK = {UserRole.ANALYST: 0, UserRole.PRICING_MANAGER: 1, UserRole.ADMIN: 2}


def require_role(minimum: UserRole) -> Callable[[User], User]:
    """Dependency factory enforcing a minimum role."""

    def _dependency(user: CurrentUser) -> User:
        if _ROLE_RANK[user.role] < _ROLE_RANK[minimum]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role '{minimum.value}' or higher.",
            )
        return user

    return _dependency


#: Ready-made aliases. Annotate with these rather than combining `CurrentUser`
#: with a `Depends(...)` default -- FastAPI rejects having both.
RequireAnalyst = Annotated[User, Depends(require_role(UserRole.ANALYST))]
RequirePricingManager = Annotated[User, Depends(require_role(UserRole.PRICING_MANAGER))]
RequireAdmin = Annotated[User, Depends(require_role(UserRole.ADMIN))]


def pagination(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


Pagination = Annotated[PageParams, Depends(pagination)]
