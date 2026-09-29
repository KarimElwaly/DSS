"""ORM models.

Importing this package registers every table on ``Base.metadata``; Alembic's
autogenerate depends on that side effect.
"""

from app.db.base import Base
from app.models.catalog import Category, CostHistory, Product, SalesFact
from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    AuditAction,
    ConnectorType,
    ElasticityMethod,
    MatchMethod,
    MatchStatus,
    ModelKind,
    PricingObjective,
    ProductStatus,
    RecommendationStatus,
    StatementType,
    StockState,
    UserRole,
)
from app.models.finance import (
    AdvisorReport,
    FinancialFinding,
    FinancialLineItem,
    FinancialMetric,
    FinancialPeriod,
)
from app.models.governance import AuditEvent
from app.models.market import (
    EMBEDDING_DIM,
    Alert,
    Competitor,
    CompetitorListing,
    MatchCandidate,
    PriceObservation,
)
from app.models.organization import Organization, User
from app.models.pricing import (
    CrossElasticity,
    ElasticityEstimate,
    ForecastPoint,
    ModelRun,
    PriceRecommendation,
    SimulationRun,
)
from app.models.validation import GroundTruth

__all__ = [
    "EMBEDDING_DIM",
    "AdvisorReport",
    "Alert",
    "AlertSeverity",
    "AlertStatus",
    "AlertType",
    "AuditAction",
    "AuditEvent",
    "Base",
    "Category",
    "Competitor",
    "CompetitorListing",
    "ConnectorType",
    "CostHistory",
    "CrossElasticity",
    "ElasticityEstimate",
    "ElasticityMethod",
    "FinancialFinding",
    "FinancialLineItem",
    "FinancialMetric",
    "FinancialPeriod",
    "ForecastPoint",
    "GroundTruth",
    "MatchCandidate",
    "MatchMethod",
    "MatchStatus",
    "ModelKind",
    "ModelRun",
    "Organization",
    "PriceObservation",
    "PriceRecommendation",
    "PricingObjective",
    "Product",
    "ProductStatus",
    "RecommendationStatus",
    "SalesFact",
    "SimulationRun",
    "StatementType",
    "StockState",
    "User",
    "UserRole",
]
