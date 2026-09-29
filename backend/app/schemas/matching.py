"""Match review and ingestion schemas (Module A, write side)."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, Field

from app.models.enums import MatchMethod, MatchStatus
from app.schemas.market import LatestObservation


class CandidateOut(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    our_price: float
    score: float
    vector_score: float
    fuzzy_score: float
    rank: int


class ReviewItem(BaseModel):
    """One listing awaiting a human decision, with its ranked suggestions."""

    listing_id: uuid.UUID
    competitor_name: str
    external_id: str
    title: str
    brand: str
    gtin: str | None
    url: str
    match_status: MatchStatus
    match_method: MatchMethod
    match_confidence: float | None
    reason: str = ""
    latest: LatestObservation | None = None
    candidates: list[CandidateOut] = Field(default_factory=list)


class MatchDecision(BaseModel):
    """Confirm a listing against a product, or reject it outright.

    ``product_id`` is required to confirm and forbidden to reject, which is
    validated in the route so the error message can name the field.
    """

    product_id: uuid.UUID | None = None
    note: str = Field(default="", max_length=500)


class MatchRunRequest(BaseModel):
    only_unmatched: bool = Field(
        default=False,
        description=(
            "Re-score only listings with no product linked. Human decisions are "
            "preserved either way."
        ),
    )


class MatchRunResponse(BaseModel):
    considered: int
    gtin: int
    auto_accepted: int
    needs_review: int
    unmatched: int
    skipped_human_decided: int


class IngestResponse(BaseModel):
    competitors: list[dict]
    new_listings: int
    new_observations: int
    failed: int
    matched: dict[str, int] = Field(default_factory=dict)
    alerts: dict[str, int] = Field(default_factory=dict)
    ran_at: dt.datetime
