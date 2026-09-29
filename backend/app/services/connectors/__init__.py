"""Competitor data sources (Module A, step 1)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import ConnectorType
from app.services.connectors.base import (
    ConnectorError,
    ConnectorResult,
    MarketConnector,
    RawListing,
    RawObservation,
)
from app.services.connectors.csv_feed import CsvConnector
from app.services.connectors.http_feed import HttpConnector
from app.services.connectors.synthetic import SyntheticConnector

__all__ = [
    "ConnectorError",
    "ConnectorResult",
    "CsvConnector",
    "HttpConnector",
    "MarketConnector",
    "RawListing",
    "RawObservation",
    "SyntheticConnector",
    "build_connector",
]


def build_connector(connector_type: ConnectorType, session: Session) -> MarketConnector:
    """Resolve a competitor's declared connector type to an implementation."""
    if connector_type == ConnectorType.SYNTHETIC:
        return SyntheticConnector(session)
    if connector_type == ConnectorType.CSV:
        return CsvConnector(base_dir=Path(get_settings().artifact_dir) / "uploads")
    if connector_type == ConnectorType.HTTP:
        return HttpConnector()
    raise ConnectorError(f"No connector implementation for {connector_type!r}.")
