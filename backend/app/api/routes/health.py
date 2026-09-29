"""Liveness / readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "env": settings.env, "version": "0.1.0"}


@router.get("/health/ready")
def readiness(session: DbSession) -> dict[str, object]:
    checks: dict[str, object] = {}
    try:
        session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - surfaced through the response
        checks["database"] = f"error: {exc.__class__.__name__}"

    try:
        session.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).one()
        checks["pgvector"] = "ok"
    except Exception:
        checks["pgvector"] = "missing"

    checks["status"] = (
        "ok" if all(v == "ok" for k, v in checks.items() if k != "status") else "degraded"
    )
    return checks
