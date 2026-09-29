from fastapi import APIRouter

from app.api.routes import analytics, auth, catalog, health, market, matching

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(catalog.router)
api_router.include_router(market.router)
api_router.include_router(matching.router)
api_router.include_router(analytics.router)

__all__ = ["api_router"]

