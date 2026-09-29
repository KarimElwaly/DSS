"""Append-only audit trail helper.

Every state-changing operation must call :func:`record_event`. The helper adds the
row to the session but does **not** commit, so the audit entry lives in the same
transaction as the change it describes.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.enums import AuditAction
from app.models.governance import AuditEvent


def record_event(
    session: Session,
    *,
    organization_id: uuid.UUID,
    action: AuditAction,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    summary: str = "",
    payload: dict[str, Any] | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_label: str = "system",
) -> AuditEvent:
    event = AuditEvent(
        organization_id=organization_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        payload=payload or {},
        actor_user_id=actor_user_id,
        actor_label=actor_label,
    )
    session.add(event)
    return event
