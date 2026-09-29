"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.services.scheduler import shutdown_scheduler, start_scheduler

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    logger.info("DSS API starting (env=%s)", settings.env)
    if settings.auth_bypass_active:
        logger.warning(
            "AUTH IS DISABLED: every request is treated as %s. "
            "This is ignored outside env=local, but never use it for a shared instance.",
            settings.admin_email,
        )
    elif settings.auth_disabled:
        logger.warning("DSS_AUTH_DISABLED is set but env=%s, so it has been ignored.", settings.env)
    start_scheduler()
    yield
    shutdown_scheduler()
    logger.info("DSS API stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Revenue & Pricing DSS",
        description=(
            "Decision support for competitive intelligence, price elasticity, "
            "dynamic pricing and financial strategy."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    origins = ["http://localhost:3000", "http://127.0.0.1:3000"] if settings.is_local else []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
