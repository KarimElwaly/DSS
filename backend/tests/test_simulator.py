"""Sanity checks for the synthetic market generator.

These are not just smoke tests: they assert the properties the estimators in
Module B rely on (price variation, cost shocks, elasticity identifiability).
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest

from simulator import MarketSimulator, default_electronics_market
from simulator.market import _canonical


@pytest.fixture(scope="module")
def market():
    config = default_electronics_market(seed=1234, history_days=365, end_date=dt.date(2026, 6, 30))
    return MarketSimulator(config).generate()


def test_generates_all_entities(market) -> None:
    summary = market.summary()
    assert summary["categories"] == 5
    assert summary["products"] == 24
    assert summary["sales_rows"] == 24 * 365
    assert summary["competitors"] == 3
    assert summary["listings"] > 40
    assert summary["price_observations"] == summary["listings"] * 365


def test_is_reproducible() -> None:
    config = default_electronics_market(seed=99, history_days=120, end_date=dt.date(2026, 6, 30))
    a = MarketSimulator(config).generate()
    b = MarketSimulator(config).generate()
    assert a.sales["units"].tolist() == b.sales["units"].tolist()
    assert a.sales["unit_price"].tolist() == b.sales["unit_price"].tolist()


def test_prices_vary_enough_for_identification(market) -> None:
    """Elasticity is unidentified without price variation -- assert it exists."""
    for sku, group in market.sales.groupby("sku"):
        cv = group["unit_price"].std() / group["unit_price"].mean()
        assert cv > 0.01, f"{sku} has near-constant price (cv={cv:.4f})"


def test_cost_shocks_exist(market) -> None:
    """Cost changes are the exogenous instrument used to break endogeneity."""
    changes = market.cost_history.groupby("sku").size()
    assert changes.min() >= 1
    assert changes.mean() > 1.5


def test_units_are_non_negative_and_finite(market) -> None:
    units = market.sales["units"].to_numpy()
    assert units.min() >= 0
    assert np.isfinite(units).all()
    assert units.mean() > 1


def test_prices_stay_above_cost(market) -> None:
    assert (market.sales["unit_price"] > market.sales["unit_cost"]).all()


def test_ground_truth_elasticities_are_elastic(market) -> None:
    for row in market.ground_truth:
        assert row["own_elasticity"] < -1.0, row["sku"]
        assert math.isfinite(row["params"]["curvature"])


def test_curvature_places_revenue_optimum_where_declared(market) -> None:
    """Local elasticity must equal -1 at the declared revenue optimum."""
    specs = {spec.sku: spec for spec in market.config.products}
    for row in market.ground_truth:
        spec = specs[row["sku"]]
        eps = row["own_elasticity"]
        gamma = spec.curvature(eps)
        x_star = math.log(1.0 + spec.revenue_optimum_offset)
        local_elasticity = eps + 2 * gamma * x_star
        assert local_elasticity == pytest.approx(-1.0, abs=1e-9)


def test_competitor_listings_carry_ground_truth_link(market) -> None:
    skus = {product["sku"] for product in market.products}
    matchable = [row for row in market.listings if row["true_product_sku"]]
    assert matchable
    assert all(row["true_product_sku"] in skus for row in matchable)
    # Some listings must hide the GTIN, otherwise the fuzzy matcher is never exercised.
    without_gtin = sum(1 for listing in market.listings if not listing["gtin"])
    assert without_gtin > 0


def test_distractor_listings_have_no_catalog_match(market) -> None:
    """Competitors must list products we do not sell.

    Without these the matcher cannot produce a false positive, so its precision
    would be 100% by construction and the auto-accept threshold untestable.
    """
    distractors = [row for row in market.listings if row["true_product_sku"] is None]
    assert distractors, "the simulator must generate hard negatives"

    catalog_gtins = {p["gtin"] for p in market.products if p["gtin"]}
    catalog_keys = {_canonical(p["brand"], p["name"]) for p in market.products}
    for row in distractors:
        # A distractor sharing a catalog GTIN or name is a mislabelled positive:
        # it would make a correct match look like an error.
        assert row["gtin"] not in catalog_gtins
        assert _canonical(row["brand"], row["title"]) not in catalog_keys


def test_distractors_are_priced_and_observed(market) -> None:
    """Hard negatives need observations, or they are invisible to the pipeline."""
    distractor_ids = {
        row["external_id"] for row in market.listings if row["true_product_sku"] is None
    }
    observed = set(market.observations["external_id"].unique())
    assert distractor_ids <= observed


def test_stockouts_occur(market) -> None:
    states = market.observations["stock_state"].value_counts()
    assert states.get("out_of_stock", 0) > 0
    assert states.get("in_stock", 0) > states.get("out_of_stock", 0)
