"""Pre-flight experiment: does competitive intelligence reduce elasticity bias?

Compares two specifications of the log-log demand model:

* **naive**    -- own price, promo, trend, seasonality, weekday
* **enriched** -- the same, plus the competitor price index and stockout fraction

The simulator makes competitors *react* to our price, so omitting the competitor
index is a textbook omitted-variable problem that attenuates the own-price
coefficient toward zero. If Module A's data closes that gap, that is a
measurable justification for the ingestion engine existing at all.

Run with:  python scripts/check_elasticity_recovery.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sqlalchemy import select

from app.db import session_scope
from app.models import (
    CompetitorListing,
    GroundTruth,
    PriceObservation,
    Product,
    SalesFact,
)
from app.models.enums import MatchStatus, StockState

ALPHA = 0.10  # -> 90% confidence intervals


def load_frames() -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    with session_scope() as session:
        products = {p.id: p for p in session.scalars(select(Product))}
        truth = {t.product_id: t for t in session.scalars(select(GroundTruth))}

        sales = pd.DataFrame(
            session.execute(
                select(
                    SalesFact.product_id,
                    SalesFact.sale_date,
                    SalesFact.units,
                    SalesFact.unit_price,
                    SalesFact.promo_flag,
                )
            ).all(),
            columns=["product_id", "sale_date", "units", "price", "promo"],
        )

        comp = pd.DataFrame(
            session.execute(
                select(
                    CompetitorListing.matched_product_id,
                    PriceObservation.observed_at,
                    PriceObservation.price,
                    PriceObservation.shipping_cost,
                    PriceObservation.stock_state,
                )
                .join(PriceObservation, PriceObservation.listing_id == CompetitorListing.id)
                .where(
                    CompetitorListing.matched_product_id.is_not(None),
                    CompetitorListing.match_status.in_(
                        (MatchStatus.AUTO_ACCEPTED, MatchStatus.CONFIRMED)
                    ),
                )
            ).all(),
            columns=["product_id", "observed_at", "price", "shipping", "stock_state"],
        )

    sales["price"] = sales["price"].astype(float)
    # NOTE: pandas 2.x will not join datetime columns of different resolutions
    # (SQLAlchemy Date -> datetime64[s], timestamptz -> datetime64[us]), and it
    # fails silently by producing all-NaN merge results. Pin both to [ns].
    sales["sale_date"] = pd.to_datetime(sales["sale_date"]).astype("datetime64[ns]")

    comp["landed"] = comp["price"].astype(float) + comp["shipping"].astype(float)
    comp["sale_date"] = (
        pd.to_datetime(comp["observed_at"], utc=True)
        .dt.tz_localize(None)
        .dt.normalize()
        .astype("datetime64[ns]")
    )
    # Compare against `.value`: once an Enum column passes through pandas it is
    # plain strings, and `series == StockState.IN_STOCK` is silently all-False.
    comp["in_stock"] = comp["stock_state"].astype(str) == StockState.IN_STOCK.value
    comp["log_landed"] = np.log(comp["landed"].where(comp["in_stock"]))

    agg = (
        comp.groupby(["product_id", "sale_date"])
        .agg(
            log_comp=("log_landed", "mean"),  # log of the geometric mean
            oos_frac=("in_stock", lambda s: 1.0 - float(np.mean(s))),
            n_listings=("in_stock", "size"),
        )
        .reset_index()
    )
    return sales, agg, products, truth


def build_design(group: pd.DataFrame, ref_price: float) -> pd.DataFrame:
    x = np.log(group["price"].to_numpy() / ref_price)
    doy = group["sale_date"].dt.dayofyear.to_numpy()
    trend = (group["sale_date"] - group["sale_date"].min()).dt.days.to_numpy() / 365.25

    design = pd.DataFrame(
        {
            "log_price": x,
            "log_price_sq": x**2,
            "promo": group["promo"].astype(float).to_numpy(),
            "trend": trend,
            "sin_y": np.sin(2 * np.pi * doy / 365.25),
            "cos_y": np.cos(2 * np.pi * doy / 365.25),
        },
        index=group.index,
    )
    for dow in range(1, 7):
        design[f"dow_{dow}"] = (group["sale_date"].dt.dayofweek == dow).astype(float)
    return design


def fit(design: pd.DataFrame, units: np.ndarray) -> tuple[float, float, float, float]:
    model = sm.OLS(np.log(units), sm.add_constant(design)).fit(cov_type="HC3")
    ci = model.conf_int(alpha=ALPHA).loc["log_price"]
    return float(model.params["log_price"]), float(ci[0]), float(ci[1]), float(model.rsquared)


def main() -> None:
    sales, comp, products, truth = load_frames()
    merged = sales.merge(comp, on=["product_id", "sale_date"], how="left")

    rows = []
    for product_id, raw in merged.groupby("product_id"):
        group = raw[raw["units"] > 0].sort_values("sale_date")
        if len(group) < 90:
            continue
        ref = truth[product_id].reference_price
        true = truth[product_id].own_elasticity
        units = group["units"].to_numpy()

        naive = build_design(group, ref)
        est_n, lo_n, hi_n, r2_n = fit(naive, units)

        enriched = naive.copy()
        has_comp = bool(group["log_comp"].notna().mean() > 0.8)
        if has_comp:
            log_comp = group["log_comp"].ffill().bfill()
            enriched["log_comp_price"] = (log_comp - log_comp.mean()).to_numpy()
            enriched["oos_frac"] = group["oos_frac"].fillna(0.0).to_numpy()
        est_e, lo_e, hi_e, r2_e = fit(enriched, units)

        rows.append(
            {
                "sku": products[product_id].sku,
                "true": true,
                "naive": est_n,
                "naive_err%": 100 * (est_n - true) / abs(true),
                "naive_cov": lo_n <= true <= hi_n,
                "enriched": est_e,
                "enr_err%": 100 * (est_e - true) / abs(true),
                "enr_cov": lo_e <= true <= hi_e,
                "comp_data": has_comp,
                "r2_naive": r2_n,
                "r2_enr": r2_e,
            }
        )

    out = pd.DataFrame(rows).sort_values("sku")
    pd.set_option("display.width", 200)
    print(out.to_string(index=False, float_format=lambda v: f"{v:7.3f}"))

    covered = out[out["comp_data"]]
    print(f"\n--- all SKUs (n={len(out)}) ---")
    _report(out)
    print(f"\n--- SKUs with competitor coverage (n={len(covered)}) ---")
    _report(covered)


def _report(frame: pd.DataFrame) -> None:
    if frame.empty:
        print("  (none)")
        return
    print(
        f"  naive    : mean|err| {frame['naive_err%'].abs().mean():6.2f}%   "
        f"bias {frame['naive_err%'].mean():+6.2f}%   "
        f"90% coverage {frame['naive_cov'].mean():5.1%}   R2 {frame['r2_naive'].mean():.3f}"
    )
    print(
        f"  enriched : mean|err| {frame['enr_err%'].abs().mean():6.2f}%   "
        f"bias {frame['enr_err%'].mean():+6.2f}%   "
        f"90% coverage {frame['enr_cov'].mean():5.1%}   R2 {frame['r2_enr'].mean():.3f}"
    )


if __name__ == "__main__":
    main()
