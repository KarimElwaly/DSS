"""Pydantic schemas package."""

from app.schemas.analytics import (
    CrossElasticityOut,
    DemandCurvePoint,
    ElasticityEstimateOut,
    FitElasticityReport,
    FitForecastReport,
    ForecastPointOut,
    SkuForecastOut,
    SkuWorkbenchOut,
)
from app.schemas.auth import Token, TokenPayload, UserLogin, UserOut
from app.schemas.catalog import CategoryOut, ProductCreate, ProductDetailOut, ProductOut, ProductUpdate
from app.schemas.common import MessageResponse, Page
from app.schemas.market import CompetitorListingOut, CompetitorOut, MarketSnapshotOut, PriceObservationOut
from app.schemas.matching import MatchCandidateOut, MatchReviewAction
from app.schemas.pricing import (
    DecideRecommendationRequest,
    GenerateRecommendationsRequest,
    PriceRecommendationOut,
    ScenarioSimulationRequest,
    SimulationRunOut,
)

__all__ = [
    "CategoryOut",
    "CompetitorListingOut",
    "CompetitorOut",
    "CrossElasticityOut",
    "DecideRecommendationRequest",
    "DemandCurvePoint",
    "ElasticityEstimateOut",
    "FitElasticityReport",
    "FitForecastReport",
    "ForecastPointOut",
    "GenerateRecommendationsRequest",
    "MarketSnapshotOut",
    "MatchCandidateOut",
    "MatchReviewAction",
    "MessageResponse",
    "Page",
    "PriceObservationOut",
    "PriceRecommendationOut",
    "ProductCreate",
    "ProductDetailOut",
    "ProductOut",
    "ProductUpdate",
    "ScenarioSimulationRequest",
    "SimulationRunOut",
    "SkuForecastOut",
    "SkuWorkbenchOut",

    "Token",
    "TokenPayload",
    "UserLogin",
    "UserOut",
]
