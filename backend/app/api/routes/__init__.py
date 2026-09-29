from fastapi import APIRouter

from app.api.routes import analytics, auth, catalog, finance, health, market, matching, pricing

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(catalog.router)
api_router.include_router(market.router)
api_router.include_router(matching.router)
api_router.include_router(analytics.router)
api_router.include_router(pricing.router)
api_router.include_router(finance.router)

__all__ = ["api_router"]



