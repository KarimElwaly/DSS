"""Pydantic schemas for Module F: CFO Advisor & Corporate Financials."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import AlertSeverity, StatementType


class FinancialLineItemOut(BaseModel):
    id: uuid.UUID
    statement_type: StatementType
    canonical_key: str
    source_label: str
    amount: float


class FinancialMetricOut(BaseModel):
    key: str
    label: str
    value: float
    unit: str
    formula: str


class FinancialPeriodOut(BaseModel):
    id: uuid.UUID
    label: str
    granularity: str
    period_start: dt.date
    period_end: dt.date
    currency: str
    metrics: dict[str, float] = Field(default_factory=dict)


class PeriodDetailOut(BaseModel):
    period: FinancialPeriodOut
    line_items: list[FinancialLineItemOut]
    metrics: list[FinancialMetricOut]


class FinancialFindingOut(BaseModel):
    id: uuid.UUID
    period_id: uuid.UUID
    period_label: str
    metric_key: str
    severity: AlertSeverity
    title: str
    detail: str
    current_value: float | None = None
    expected_value: float | None = None
    delta_pct: float | None = None
    z_score: float | None = None


class AdvisorReportOut(BaseModel):
    id: uuid.UUID
    period_id: uuid.UUID | None = None
    model_name: str
    facts: dict[str, Any]
    narrative: str
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    grounding_passed: bool
    grounding_violations: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
    created_at: dt.datetime


class UploadStatementsRequest(BaseModel):
    label: str
    period_start: dt.date
    period_end: dt.date
    granularity: str = "quarter"
    items: dict[str, float]
