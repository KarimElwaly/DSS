"""Pydantic schemas for Modules C & D: Dynamic Pricing and Scenario Sandbox."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import PricingObjective, RecommendationStatus


class PriceRecommendationOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    product_name: str
    category_name: str | None = None
    objective: PricingObjective
    current_price: float
    recommended_price: float
    unconstrained_price: float | None = None
    price_floor: float
    price_ceiling: float
    expected_units: float | None = None
    expected_revenue: float | None = None
    expected_margin: float | None = None
    baseline_revenue: float | None = None
    baseline_margin: float | None = None
    elasticity_used: float | None = None
    confidence: float | None = None
    binding_constraints: list[str] = Field(default_factory=list)
    rationale: dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""
    status: RecommendationStatus
    created_at: dt.datetime
    decided_at: dt.datetime | None = None
    decision_note: str = ""


class GenerateRecommendationsRequest(BaseModel):
    objective: PricingObjective = PricingObjective.MARGIN
    product_ids: list[uuid.UUID] | None = None
    apply_charm_rounding: bool = True


class DecideRecommendationRequest(BaseModel):
    status: RecommendationStatus
    note: str = ""


class ScenarioSimulationRequest(BaseModel):
    name: str = "What-if Scenario"
    horizon_days: int = 30
    price_overrides: dict[uuid.UUID, float] = Field(default_factory=dict)
    competitor_reaction_pct: float = 0.30
    n_monte_carlo: int = 500


class SimulationRunOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: dt.datetime
    horizon_days: int
    scenario: dict[str, Any]
    results: dict[str, Any]
    n_monte_carlo: int
