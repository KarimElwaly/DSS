"""Enumerations shared across ORM models and API schemas.

Stored as ``VARCHAR + CHECK`` (``native_enum=False``) so that adding a member is a
trivial migration instead of a PostgreSQL ``ALTER TYPE``.
"""

from __future__ import annotations

from enum import Enum


class UserRole(str, Enum):
    ADMIN = "admin"
    PRICING_MANAGER = "pricing_manager"
    ANALYST = "analyst"


class ProductStatus(str, Enum):
    ACTIVE = "active"
    DISCONTINUED = "discontinued"
    END_OF_LIFE = "end_of_life"


class ConnectorType(str, Enum):
    SYNTHETIC = "synthetic"
    CSV = "csv"
    HTTP = "http"


class MatchMethod(str, Enum):
    GTIN = "gtin"
    EMBEDDING = "embedding"
    MANUAL = "manual"
    UNMATCHED = "unmatched"


class MatchStatus(str, Enum):
    PENDING = "pending"
    AUTO_ACCEPTED = "auto_accepted"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class StockState(str, Enum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


class AlertType(str, Enum):
    COMPETITOR_STOCKOUT = "competitor_stockout"
    COMPETITOR_RESTOCK = "competitor_restock"
    PRICE_UNDERCUT = "price_undercut"
    MARGIN_BREACH = "margin_breach"
    DATA_STALE = "data_stale"
    FINANCIAL_ANOMALY = "financial_anomaly"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class PricingObjective(str, Enum):
    REVENUE = "revenue"
    MARGIN = "margin"
    PENETRATION = "penetration"


class RecommendationStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    EXPIRED = "expired"


class ElasticityMethod(str, Enum):
    LOG_LOG_OLS = "log_log_ols"
    POOLED_CATEGORY = "pooled_category"
    PRIOR_FALLBACK = "prior_fallback"


class ModelKind(str, Enum):
    ELASTICITY = "elasticity"
    FORECAST = "forecast"
    ANOMALY = "anomaly"
    MATCHING = "matching"


class StatementType(str, Enum):
    INCOME_STATEMENT = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"
    OPERATING_KPI = "operating_kpi"


class AuditAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    APPROVE = "approve"
    REJECT = "reject"
    APPLY = "apply"
    INGEST = "ingest"
    TRAIN = "train"
    SIMULATE = "simulate"
    LOGIN = "login"
