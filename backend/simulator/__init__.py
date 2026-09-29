"""Synthetic market generator with **known** ground-truth parameters.

This package is the validation backbone of the project: because the true price
elasticity of every SKU is chosen here, the estimators in ``app.analytics`` can
be scored on recovery error rather than on unverifiable business intuition.

Design rules that keep the generated data honest:

1. **Price must vary** -- promotions, cost pass-through and competitor reactions
   move price around, otherwise elasticity is not identified.
2. **Cost shocks are exogenous** -- they shift price without shifting demand,
   which gives Module B a usable instrument.
3. **Demand is generated from a constant-elasticity model with multiplicative
   noise**, so a log-log regression is the correctly specified estimator. Any
   recovery error is then attributable to sample size, confounding and
   endogeneity rather than to model misspecification.
"""

from simulator.config import (
    CategorySpec,
    CompetitorSpec,
    MarketConfig,
    ProductSpec,
    default_electronics_market,
)
from simulator.market import GeneratedMarket, MarketSimulator

__all__ = [
    "CategorySpec",
    "CompetitorSpec",
    "GeneratedMarket",
    "MarketConfig",
    "MarketSimulator",
    "ProductSpec",
    "default_electronics_market",
]
