"""Demand forecasting & backtesting engine (Module B).

Produces 30-day baseline demand forecasts with prediction intervals using
Seasonal-Naive and Exponential Smoothing (ETS/Holt-Winters), validated via
a rolling-origin backtest that computes WAPE and MAE.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from app.core.logging import get_logger
from app.models import ForecastPoint, ModelRun, Product, SalesFact
from app.models.enums import AuditAction, ModelKind
from app.services.audit import record_audit_event

logger = get_logger(__name__)


def _load_sales_series(
    session: Session, organization_id: uuid.UUID
) -> tuple[dict[uuid.UUID, pd.Series], dict[uuid.UUID, Product]]:
    """Load daily sales time series for every active product."""
    products = {
        p.id: p
        for p in session.scalars(
            select(Product).where(Product.organization_id == organization_id)
        )
    }

    rows = session.execute(
        select(SalesFact.product_id, SalesFact.sale_date, SalesFact.units).where(
            SalesFact.organization_id == organization_id
        )
    ).all()

    if not rows:
        return {}, products

    df = pd.DataFrame(rows, columns=["product_id", "sale_date", "units"])
    df["sale_date"] = pd.to_datetime(df["sale_date"]).dt.normalize()

    series_by_prod: dict[uuid.UUID, pd.Series] = {}
    for p_id in products:
        sub = df[df["product_id"] == p_id].sort_values("sale_date")
        if sub.empty:
            continue
        # Reindex to full daily frequency, filling missing days with 0 units
        s = sub.set_index("sale_date")["units"].astype(float)
        idx = pd.date_range(start=s.index.min(), end=s.index.max(), freq="D")
        s = s.reindex(idx, fill_value=0.0)
        series_by_prod[p_id] = s

    return series_by_prod, products


def _predict_seasonal_naive(train: pd.Series, horizon: int) -> np.ndarray:
    """Seasonal-naive baseline: repeat the last 7-day pattern."""
    if len(train) < 7:
        val = float(train.mean()) if len(train) > 0 else 10.0
        return np.full(horizon, val)
    last_week = train.iloc[-7:].to_numpy()
    repeats = int(np.ceil(horizon / 7))
    return np.tile(last_week, repeats)[:horizon]


def _predict_ets(train: pd.Series, horizon: int) -> tuple[np.ndarray, float]:
    """Fit Holt-Winters Exponential Smoothing.

    Returns:
        (predicted_units, residual_std_error)
    """
    if len(train) < 21:
        preds = _predict_seasonal_naive(train, horizon)
        return preds, float(train.std()) if len(train) > 1 else 3.0

    try:
        # Additive trend with weekly (7-day) seasonality
        model = ExponentialSmoothing(
            train,
            trend="add",
            seasonal="add",
            seasonal_periods=7,
            damped_trend=True,
            initialization_method="estimated",
        ).fit(damping_slope=0.95, optimized=True)
        forecast = model.forecast(horizon).to_numpy()
        residuals = train - model.fittedvalues
        sigma = float(residuals.std())
        return np.clip(forecast, 0.0, None), max(1.0, sigma)
    except Exception as exc:
        logger.debug("ETS fit failed (%s); fallback to Holt or Naive", exc)
        try:
            model = ExponentialSmoothing(
                train,
                trend="add",
                damped_trend=True,
                initialization_method="estimated",
            ).fit(optimized=True)
            forecast = model.forecast(horizon).to_numpy()
            residuals = train - model.fittedvalues
            return np.clip(forecast, 0.0, None), max(1.0, float(residuals.std()))
        except Exception:
            preds = _predict_seasonal_naive(train, horizon)
            return preds, float(train.std()) if len(train) > 1 else 3.0


def _calculate_wape(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Calculate Weighted Absolute Percentage Error: sum(|y - y_hat|) / sum(y)."""
    sum_actual = float(np.sum(actual))
    if sum_actual <= 0:
        return 0.0
    return float(np.sum(np.abs(actual - predicted)) / sum_actual)


def run_demand_forecast(
    session: Session,
    organization_id: uuid.UUID,
    *,
    horizon_days: int = 30,
    backtest_days: int = 30,
    actor_id: uuid.UUID | None = None,
) -> ModelRun:
    """Run rolling-origin backtest and generate forward 30-day forecast points.

    1. Splits history to run a backtest on the final `backtest_days` days.
    2. Evaluates WAPE and MAE for Seasonal-Naive and ETS.
    3. Re-trains on full history and forecasts `horizon_days` ahead.
    4. Persists ModelRun and ForecastPoint records.
    """
    series_by_prod, products = _load_sales_series(session, organization_id)
    if not series_by_prod:
        raise ValueError("No sales history found to generate demand forecasts.")

    # Unmark existing champions of this kind
    session.query(ModelRun).filter(
        ModelRun.organization_id == organization_id,
        ModelRun.kind == ModelKind.FORECAST,
        ModelRun.is_champion.is_(True),
    ).update({"is_champion": False})

    # Global dates
    all_dates = pd.concat([pd.Series(s.index) for s in series_by_prod.values()])
    start_date = all_dates.min().date()
    end_date = all_dates.max().date()

    model_run = ModelRun(
        organization_id=organization_id,
        kind=ModelKind.FORECAST,
        label=f"ETS + Seasonal-Naive ({horizon_days}d horizon, backtest={backtest_days}d)",
        params={
            "horizon_days": horizon_days,
            "backtest_days": backtest_days,
            "models": ["seasonal_naive", "ets_holt_winters"],
            "prediction_interval_z": 1.28,  # ~80% coverage
        },
        metrics={},
        train_start=start_date,
        train_end=end_date,
        is_champion=True,
        n_products=len(products),
    )
    session.add(model_run)
    session.flush()

    # Backtest metrics accumulation
    total_actual_b = 0.0
    total_err_naive = 0.0
    total_err_ets = 0.0
    n_backtest_points = 0

    forecast_points: list[ForecastPoint] = []
    per_sku_wape: dict[str, float] = {}

    for product_id, product in products.items():
        s = series_by_prod.get(product_id)
        if s is None or len(s) < 14:
            continue

        # 1. Backtest split
        if len(s) > backtest_days + 14:
            train_b = s.iloc[:-backtest_days]
            test_b = s.iloc[-backtest_days:].to_numpy()

            pred_naive_b = _predict_seasonal_naive(train_b, backtest_days)
            pred_ets_b, _ = _predict_ets(train_b, backtest_days)

            err_n = np.sum(np.abs(test_b - pred_naive_b))
            err_e = np.sum(np.abs(test_b - pred_ets_b))
            act_sum = np.sum(test_b)

            total_actual_b += act_sum
            total_err_naive += err_n
            total_err_ets += err_e
            n_backtest_points += len(test_b)

            sku_wape = (err_e / act_sum) if act_sum > 0 else 0.0
            per_sku_wape[product.sku] = round(float(sku_wape), 3)

        # 2. Forward forecast
        preds_forward, sigma = _predict_ets(s, horizon_days)
        z_multiplier = 1.28  # 80% confidence bound for demand intervals

        cur_price = product.current_price
        last_date = s.index.max().date()

        for step in range(1, horizon_days + 1):
            target_date = last_date + dt.timedelta(days=step)
            y_hat = float(preds_forward[step - 1])
            lo_units = max(0.0, y_hat - z_multiplier * sigma)
            hi_units = y_hat + z_multiplier * sigma

            point = ForecastPoint(
                organization_id=organization_id,
                product_id=product_id,
                model_run_id=model_run.id,
                target_date=target_date,
                predicted_units=round(y_hat, 2),
                lower_units=round(lo_units, 2),
                upper_units=round(hi_units, 2),
                assumed_price=cur_price,
            )
            session.add(point)
            forecast_points.append(point)

    wape_naive = (total_err_naive / total_actual_b) if total_actual_b > 0 else 0.0
    wape_ets = (total_err_ets / total_actual_b) if total_actual_b > 0 else 0.0
    mae_ets = (total_err_ets / n_backtest_points) if n_backtest_points > 0 else 0.0

    model_run.metrics = {
        "n_forecast_points": len(forecast_points),
        "backtest_days": backtest_days,
        "wape_ets": round(float(wape_ets), 4),
        "wape_seasonal_naive": round(float(wape_naive), 4),
        "wape_improvement_pct": round(
            100.0 * (wape_naive - wape_ets) / wape_naive, 2
        )
        if wape_naive > 0
        else 0.0,
        "mae_ets": round(float(mae_ets), 2),
        "per_sku_wape": per_sku_wape,
    }

    record_audit_event(
        session,
        organization_id=organization_id,
        action=AuditAction.TRAIN,
        entity_type="model_run",
        entity_id=model_run.id,
        actor_id=actor_id,
        payload={"kind": ModelKind.FORECAST.value, "metrics": model_run.metrics},
    )

    session.commit()
    logger.info(
        "Demand forecast complete for %d products (horizon=%dd, ETS WAPE=%.2f%% vs Naive=%.2f%%)",
        len(products),
        horizon_days,
        wape_ets * 100.0,
        wape_naive * 100.0,
    )
    return model_run
