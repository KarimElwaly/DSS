"""Pydantic schemas for Module B analytics (elasticity & forecasting)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import ElasticityMethod


class DemandCurvePoint(BaseModel):
    price: float
    units: float
    lower_units: float
    upper_units: float
    revenue: float
    margin: float


class CrossElasticityOut(BaseModel):
    id: uuid.UUID
    related_product_id: uuid.UUID
    related_sku: str
    related_name: str
    elasticity: float
    std_error: float | None = None
    p_value: float | None = None
    is_significant: bool = False


class ElasticityEstimateOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    product_name: str
    category_name: str | None = None
    method: ElasticityMethod
    elasticity: float
    std_error: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    p_value: float | None = None
    r_squared: float | None = None
    n_observations: int
    price_variation_cv: float | None = None
    is_reliable: bool
    current_price: float
    reference_price: float | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class SkuWorkbenchOut(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    brand: str
    category_name: str | None = None
    current_price: float
    unit_cost: float
    min_margin_pct: float
    estimate: ElasticityEstimateOut | None = None
    demand_curve: list[DemandCurvePoint] = Field(default_factory=list)
    cross_elasticities: list[CrossElasticityOut] = Field(default_factory=list)


class FitElasticityReport(BaseModel):
    model_run_id: uuid.UUID
    label: str
    created_at: dt.datetime
    n_products: int
    metrics: dict[str, Any]


class ForecastPointOut(BaseModel):
    target_date: dt.date
    predicted_units: float
    lower_units: float | None = None
    upper_units: float | None = None
    assumed_price: float | None = None


class SkuForecastOut(BaseModel):
    product_id: uuid.UUID
    sku: str
    model_run_id: uuid.UUID
    horizon_days: int
    points: list[ForecastPointOut] = Field(default_factory=list)
    backtest_wape: float | None = None


class FitForecastReport(BaseModel):
    model_run_id: uuid.UUID
    label: str
    created_at: dt.datetime
    n_products: int
    metrics: dict[str, Any]
