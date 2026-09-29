"""Generation of a synthetic market with known ground-truth elasticities."""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

from simulator.config import (
    CategorySpec,
    CompetitorSpec,
    MarketConfig,
    ProductSpec,
    default_electronics_market,
)

GENERATOR_VERSION = "1.0"

_STOCKOUT_BOOST = 0.18
"""True coefficient on the fraction of rivals that are out of stock.

This is the ground truth behind the 'raise price when competitors stock out'
opportunity rule in Module A.
"""

#: Fictional manufacturers we do not stock. Kept disjoint from the catalog's own
#: brands by an assertion in ``_make_distractors`` -- reusing one of our brands
#: would silently turn a hard negative into a mislabelled positive.
_RIVAL_BRANDS = ("Lumen", "Cobalt", "Quasar", "Verity", "Ravenna", "Bluefin", "Calder", "Sable")
_RIVAL_MODELS = ("Vertex", "Halo", "Onyx", "Meridian", "Drift", "Zenith")

#: Trims and capacities we never stocked. Appended to a *stem* (the model name
#: with its own trim stripped) so the result is a sibling of a real SKU rather
#: than a duplicate of one.
_UNSTOCKED_VARIANTS = (
    "Pro Max 512GB",
    "Lite 64GB",
    "SE 128GB",
    "Ultra 1TB",
    "Mini 32GB",
    "Plus 512GB",
)

#: Trailing trim/capacity tokens, stripped repeatedly to reach the model stem.
_TRAILING_VARIANT = re.compile(
    r"\s+(?:\d+\s*(?:gb|tb|mb)|\d+\s*mm|lite|plus|pro|ultra|se|mini|studio|sport"
    r"|active|max|wifi|black|white)$",
    re.IGNORECASE,
)


def _model_stem(name: str) -> str:
    """Strip trailing trim and capacity tokens: 'Orbit 3 Sport 46mm' -> 'Orbit 3'."""
    stem = name.strip()
    while True:
        shorter = _TRAILING_VARIANT.sub("", stem).strip()
        if shorter == stem or not shorter:
            return stem
        stem = shorter


def _canonical(brand: str, name: str) -> str:
    """Comparison key used to prove a distractor is not a real product."""
    return re.sub(r"[^a-z0-9]+", "", f"{brand} {name}".lower())


def charm_round(price: float) -> float:
    """Apply a psychological price ending (``648.99``)."""
    if price < 10:
        return round(price, 2)
    return float(round(price) - 0.01)


def _weekly_factor(day_of_week: int) -> float:
    # Mon..Sun multiplicative log-effects; weekends sell more.
    table = (-0.05, -0.07, -0.04, 0.01, 0.09, 0.14, 0.06)
    return table[day_of_week]


@dataclass
class GeneratedMarket:
    """Everything needed to populate the database, keyed by natural identifiers."""

    config: MarketConfig
    categories: list[dict[str, Any]] = field(default_factory=list)
    products: list[dict[str, Any]] = field(default_factory=list)
    cost_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    sales: pd.DataFrame = field(default_factory=pd.DataFrame)
    competitors: list[dict[str, Any]] = field(default_factory=list)
    listings: list[dict[str, Any]] = field(default_factory=list)
    observations: pd.DataFrame = field(default_factory=pd.DataFrame)
    ground_truth: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "categories": len(self.categories),
            "products": len(self.products),
            "cost_history_rows": len(self.cost_history),
            "sales_rows": len(self.sales),
            "competitors": len(self.competitors),
            "listings": len(self.listings),
            "price_observations": len(self.observations),
        }


class MarketSimulator:
    """Generates catalog, price paths, demand and competitor observations.

    The demand model is intentionally simple and *correctly specified* for a
    log-log regression::

        ln q_t = ln(base)
                 + eps * x_t + gamma * x_t^2                 # own price
                 + eta  * ln(C_t / C_ref)                    # competitor index
                 + sum_j kappa_j * ln(p_jt / p_j_ref)        # sibling SKUs
                 + season_t + weekday_t + trend_t
                 + lift * promo_t + boost * oos_frac_t
                 + e_t,      e_t ~ N(0, sigma^2)

    ``units_t ~ Poisson(exp(ln q_t))`` adds realistic count noise.
    """

    def __init__(self, config: MarketConfig | None = None) -> None:
        self.config = config or default_electronics_market()
        self.rng = np.random.default_rng(self.config.seed)
        self._categories: dict[str, CategorySpec] = {c.slug: c for c in self.config.categories}

    # ------------------------------------------------------------------ run
    def generate(self) -> GeneratedMarket:
        cfg = self.config
        start = cfg.start_date()
        dates = pd.date_range(start, periods=cfg.history_days, freq="D")
        n_days = len(dates)

        specs = list(cfg.products)
        elasticities = self._draw_elasticities(specs)

        cost_paths, price_paths, promo_flags = self._simulate_own_prices(specs, n_days)
        competitor_state = self._simulate_competitors(specs, price_paths, dates)

        sales = self._simulate_demand(
            specs, elasticities, price_paths, cost_paths, promo_flags, competitor_state, dates
        )

        market = GeneratedMarket(config=cfg)
        market.categories = [
            {"name": c.name, "slug": c.slug, "elasticity_mean": c.elasticity_mean}
            for c in cfg.categories
        ]
        market.competitors = [
            {
                "name": c.name,
                "slug": c.slug,
                "domain": c.domain,
                "price_index": c.price_index,
            }
            for c in cfg.competitors
        ]
        market.products = self._build_products(specs, cost_paths, price_paths, start, n_days)
        market.cost_history = self._build_cost_history(specs, cost_paths, dates)
        market.sales = sales
        market.listings = competitor_state["listings"]
        market.observations = competitor_state["observations"]
        market.ground_truth = self._build_ground_truth(specs, elasticities, competitor_state)
        return market

    # ------------------------------------------------------------ internals
    def _draw_elasticities(self, specs: list[ProductSpec]) -> dict[str, float]:
        out: dict[str, float] = {}
        for spec in specs:
            cat = self._categories[spec.category_slug]
            value = float(self.rng.normal(cat.elasticity_mean, cat.elasticity_sd))
            # Keep demand elastic so the margin optimum stays finite and positive.
            out[spec.sku] = float(np.clip(value, -4.0, -1.25))
        return out

    def _simulate_own_prices(
        self, specs: list[ProductSpec], n_days: int
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray]]:
        cfg = self.config
        cost_paths: dict[str, np.ndarray] = {}
        price_paths: dict[str, np.ndarray] = {}
        promo_flags: dict[str, np.ndarray] = {}

        for spec in specs:
            cost = np.empty(n_days)
            list_price = np.empty(n_days)
            promo = np.zeros(n_days, dtype=bool)

            current_cost = spec.base_cost
            # log-deviation of list price from its launch value
            drift = 0.0
            promo_days_left = 0
            promo_depth = 0.0

            for t in range(n_days):
                # -- exogenous supplier cost shock (the instrument) ---------
                if self.rng.random() < cfg.cost_shock_rate:
                    current_cost *= float(math.exp(self.rng.normal(0.0, cfg.cost_shock_sd)))
                    current_cost = max(current_cost, 0.25 * spec.base_cost)
                    # pass a fraction of the cost move through to list price
                    cost_gap = math.log(current_cost / spec.base_cost)
                    drift = cfg.cost_pass_through * cost_gap + (1 - cfg.cost_pass_through) * drift

                # -- deliberate price experimentation (mean-reverting walk) -
                drift += float(self.rng.normal(0.0, cfg.price_walk_sd))
                drift -= cfg.price_walk_reversion * drift
                drift = float(np.clip(drift, -cfg.max_price_drift, cfg.max_price_drift))

                cost[t] = current_cost
                base = spec.launch_price * math.exp(drift)

                # -- promotions --------------------------------------------
                if promo_days_left > 0:
                    promo_days_left -= 1
                    promo[t] = True
                elif self.rng.random() < cfg.promo_rate:
                    promo_days_left = max(1, int(self.rng.poisson(cfg.promo_mean_days)))
                    low, high = cfg.promo_depth_range
                    promo_depth = float(self.rng.uniform(low, high))
                    promo[t] = True
                    promo_days_left -= 1

                effective = base * (1.0 - promo_depth) if promo[t] else base
                # never sell below cost in the generated history
                effective = max(effective, current_cost * 1.02)
                list_price[t] = charm_round(effective)

            cost_paths[spec.sku] = cost
            price_paths[spec.sku] = list_price
            promo_flags[spec.sku] = promo
        return cost_paths, price_paths, promo_flags

    def _simulate_competitors(
        self,
        specs: list[ProductSpec],
        price_paths: dict[str, np.ndarray],
        dates: pd.DatetimeIndex,
    ) -> dict[str, Any]:
        cfg = self.config
        n_days = len(dates)
        listings: list[dict[str, Any]] = []
        obs_rows: list[dict[str, Any]] = []
        # sku -> array of geometric-mean competitor price (in-stock only)
        comp_index: dict[str, np.ndarray] = {}
        comp_ref: dict[str, float] = {}
        oos_fraction: dict[str, np.ndarray] = {}

        per_sku_prices: dict[str, list[np.ndarray]] = {s.sku: [] for s in specs}
        per_sku_instock: dict[str, list[np.ndarray]] = {s.sku: [] for s in specs}

        for comp in cfg.competitors:
            carried: list[ProductSpec] = []
            for spec in specs:
                if self.rng.random() > comp.coverage:
                    continue
                carried.append(spec)
                external_id = f"{comp.slug.upper()}-{spec.sku}"
                listings.append(
                    {
                        "competitor_slug": comp.slug,
                        "external_id": external_id,
                        "title": self._mangle_title(spec, comp),
                        "brand": "" if self.rng.random() < comp.title_noise * 0.4 else spec.brand,
                        "gtin": spec.gtin if self.rng.random() < comp.gtin_publish_rate else None,
                        "description": f"{spec.description} available at {comp.name}.",
                        "url": f"https://{comp.domain}/p/{external_id.lower()}",
                        # Ground-truth link, used only to score the matcher.
                        "true_product_sku": spec.sku,
                    }
                )

                prices, in_stock, promo = self._competitor_price_path(
                    comp, price_paths[spec.sku], n_days
                )
                per_sku_prices[spec.sku].append(prices)
                per_sku_instock[spec.sku].append(in_stock)

                for t in range(n_days):
                    obs_rows.append(
                        {
                            "competitor_slug": comp.slug,
                            "external_id": external_id,
                            "observed_at": dates[t].to_pydatetime(),
                            "price": round(float(prices[t]), 2),
                            "shipping_cost": 0.0 if float(prices[t]) >= 50 else 4.99,
                            "stock_state": "in_stock" if in_stock[t] else "out_of_stock",
                            "promo_flag": bool(promo[t]),
                        }
                    )

            # -- products this rival sells that we do not ----------------------
            # Their prices are never folded into `per_sku_prices`, so they cannot
            # reach the demand model. They exist purely as hard negatives for the
            # matcher: correct behaviour is to leave every one of them unmatched.
            for distractor in self._make_distractors(comp, carried, specs):
                base_price = float(distractor.pop("_base_price"))
                listings.append(distractor)
                prices, in_stock, promo = self._competitor_price_path(
                    comp, np.full(n_days, base_price), n_days
                )
                for t in range(n_days):
                    obs_rows.append(
                        {
                            "competitor_slug": comp.slug,
                            "external_id": distractor["external_id"],
                            "observed_at": dates[t].to_pydatetime(),
                            "price": round(float(prices[t]), 2),
                            "shipping_cost": 0.0 if float(prices[t]) >= 50 else 4.99,
                            "stock_state": "in_stock" if in_stock[t] else "out_of_stock",
                            "promo_flag": bool(promo[t]),
                        }
                    )

        for spec in specs:
            price_stack = per_sku_prices[spec.sku]
            if not price_stack:
                comp_index[spec.sku] = np.full(n_days, spec.launch_price)
                comp_ref[spec.sku] = spec.launch_price
                oos_fraction[spec.sku] = np.zeros(n_days)
                continue
            prices = np.vstack(price_stack)
            stock = np.vstack(per_sku_instock[spec.sku]).astype(float)
            n_listed = stock.shape[0]
            oos = 1.0 - stock.mean(axis=0)

            # Geometric mean over in-stock rivals; carry forward when all are out.
            log_prices = np.log(prices)
            weights = stock
            weight_sum = weights.sum(axis=0)
            idx = np.empty(n_days)
            last = float(np.exp(log_prices[:, 0].mean()))
            for t in range(n_days):
                if weight_sum[t] > 0:
                    last = float(np.exp((log_prices[:, t] * weights[:, t]).sum() / weight_sum[t]))
                idx[t] = last
            comp_index[spec.sku] = idx
            comp_ref[spec.sku] = float(np.exp(np.log(prices[:, :28]).mean()))
            oos_fraction[spec.sku] = oos
            _ = n_listed

        observations = pd.DataFrame(obs_rows)
        return {
            "listings": listings,
            "observations": observations,
            "comp_index": comp_index,
            "comp_ref": comp_ref,
            "oos_fraction": oos_fraction,
        }

    def _competitor_price_path(
        self, comp: CompetitorSpec, our_price: np.ndarray, n_days: int
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        prices = np.empty(n_days)
        in_stock = np.ones(n_days, dtype=bool)
        promo = np.zeros(n_days, dtype=bool)

        level = float(our_price[0] * comp.price_index)
        stockout_left = 0
        promo_left = 0
        promo_depth = 0.0

        for t in range(n_days):
            target = float(our_price[t] * comp.price_index)
            level += comp.reaction_speed * (target - level)
            noisy = level * float(math.exp(self.rng.normal(0.0, comp.price_noise)))

            if promo_left > 0:
                promo_left -= 1
                promo[t] = True
            elif self.rng.random() < comp.promo_rate:
                promo_left = max(1, int(self.rng.poisson(3.0))) - 1
                promo_depth = float(self.rng.uniform(0.05, 0.20))
                promo[t] = True

            prices[t] = charm_round(noisy * (1.0 - promo_depth) if promo[t] else noisy)

            if stockout_left > 0:
                stockout_left -= 1
                in_stock[t] = False
            elif self.rng.random() < comp.stockout_rate:
                stockout_left = max(1, int(self.rng.poisson(comp.stockout_mean_days))) - 1
                in_stock[t] = False

        return prices, in_stock, promo

    def _simulate_demand(
        self,
        specs: list[ProductSpec],
        elasticities: dict[str, float],
        price_paths: dict[str, np.ndarray],
        cost_paths: dict[str, np.ndarray],
        promo_flags: dict[str, np.ndarray],
        competitor_state: dict[str, Any],
        dates: pd.DatetimeIndex,
    ) -> pd.DataFrame:
        n_days = len(dates)
        doy = dates.dayofyear.to_numpy()
        dow = dates.dayofweek.to_numpy()
        t_years = np.arange(n_days) / 365.25
        weekday = np.array([_weekly_factor(int(d)) for d in dow])

        rows: list[dict[str, Any]] = []
        for spec in specs:
            cat = self._categories[spec.category_slug]
            eps = elasticities[spec.sku]
            gamma = spec.curvature(eps)
            price = price_paths[spec.sku]
            cost = cost_paths[spec.sku]
            promo = promo_flags[spec.sku]

            x = np.log(price / spec.launch_price)
            seasonal = cat.seasonal_amplitude * np.cos(
                2 * np.pi * (doy - cat.seasonal_peak_doy) / 365.25
            )
            comp_idx = competitor_state["comp_index"][spec.sku]
            comp_ref = competitor_state["comp_ref"][spec.sku]
            comp_term = spec.competitor_cross_elasticity * np.log(comp_idx / comp_ref)
            oos = competitor_state["oos_fraction"][spec.sku]

            sibling_term = np.zeros(n_days)
            for sibling in spec.substitutes:
                if sibling not in price_paths:
                    continue
                sib_spec = next(s for s in specs if s.sku == sibling)
                sibling_term += spec.substitute_cross_elasticity * np.log(
                    price_paths[sibling] / sib_spec.launch_price
                )

            log_mu = (
                math.log(spec.base_demand)
                + eps * x
                + gamma * x**2
                + comp_term
                + sibling_term
                + seasonal
                + weekday
                + spec.trend_per_year * t_years
                + spec.promo_lift * promo.astype(float)
                + _STOCKOUT_BOOST * oos
            )
            noise = self.rng.normal(0.0, spec.noise_sigma, n_days)
            mu = np.exp(log_mu + noise)
            units = self.rng.poisson(np.clip(mu, 0.0, 1e6))

            for t in range(n_days):
                rows.append(
                    {
                        "sku": spec.sku,
                        "sale_date": dates[t].date(),
                        "channel": "web",
                        "units": int(units[t]),
                        "unit_price": round(float(price[t]), 2),
                        "unit_cost": round(float(cost[t]), 2),
                        "promo_flag": bool(promo[t]),
                    }
                )
        return pd.DataFrame(rows)

    def _build_products(
        self,
        specs: list[ProductSpec],
        cost_paths: dict[str, np.ndarray],
        price_paths: dict[str, np.ndarray],
        start: dt.date,
        n_days: int,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for spec in specs:
            cat = self._categories[spec.category_slug]
            out.append(
                {
                    "sku": spec.sku,
                    "name": spec.name,
                    "brand": spec.brand,
                    "gtin": spec.gtin,
                    "description": spec.description,
                    "category_slug": spec.category_slug,
                    "current_price": round(float(price_paths[spec.sku][-1]), 2),
                    "unit_cost": round(float(cost_paths[spec.sku][-1]), 2),
                    "msrp": round(spec.launch_price * 1.10, 2),
                    "map_price": round(spec.launch_price * 0.80, 2),
                    "min_margin_pct": cat.min_margin_pct,
                    "max_price_step_pct": 0.15,
                    "launch_date": start - dt.timedelta(days=30),
                    "attributes": dict(spec.attributes),
                }
            )
        _ = n_days
        return out

    def _build_cost_history(
        self,
        specs: list[ProductSpec],
        cost_paths: dict[str, np.ndarray],
        dates: pd.DatetimeIndex,
    ) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for spec in specs:
            costs = cost_paths[spec.sku]
            previous: float | None = None
            for t, value in enumerate(costs):
                rounded = round(float(value), 2)
                if previous is None or abs(rounded - previous) > 1e-9:
                    rows.append(
                        {
                            "sku": spec.sku,
                            "valid_from": dates[t].date(),
                            "unit_cost": rounded,
                            "note": "initial" if previous is None else "supplier cost shock",
                        }
                    )
                    previous = rounded
        return pd.DataFrame(rows)

    def _build_ground_truth(
        self,
        specs: list[ProductSpec],
        elasticities: dict[str, float],
        competitor_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for spec in specs:
            cat = self._categories[spec.category_slug]
            eps = elasticities[spec.sku]
            out.append(
                {
                    "sku": spec.sku,
                    "generator_version": GENERATOR_VERSION,
                    "seed": self.config.seed,
                    "own_elasticity": eps,
                    "competitor_cross_elasticity": spec.competitor_cross_elasticity,
                    "base_demand": spec.base_demand,
                    "reference_price": spec.launch_price,
                    "promo_lift": spec.promo_lift,
                    "trend_per_year": spec.trend_per_year,
                    "seasonal_amplitude": cat.seasonal_amplitude,
                    "noise_sigma": spec.noise_sigma,
                    "sibling_cross_elasticities": dict.fromkeys(
                        spec.substitutes, spec.substitute_cross_elasticity
                    ),
                    "params": {
                        "curvature": spec.curvature(eps),
                        "revenue_optimum_price": round(
                            spec.launch_price * (1.0 + spec.revenue_optimum_offset), 2
                        ),
                        "margin_optimum_price": round(self._margin_optimum(spec, eps), 2),
                        "stockout_boost": _STOCKOUT_BOOST,
                        "competitor_reference_price": round(
                            float(competitor_state["comp_ref"].get(spec.sku, spec.launch_price)), 2
                        ),
                    },
                }
            )
        return out

    @staticmethod
    def _margin_optimum(spec: ProductSpec, elasticity: float) -> float:
        """Lerner-rule optimum for the constant-elasticity part of demand."""
        if elasticity >= -1.0:
            return spec.launch_price
        return spec.base_cost * elasticity / (1.0 + elasticity)

    def _make_distractors(
        self, comp: CompetitorSpec, carried: list[ProductSpec], catalog: list[ProductSpec]
    ) -> list[dict[str, Any]]:
        """Listings for products outside our catalog.

        Two flavours, because they fail differently:

        *Rival brands* are easy negatives -- different vocabulary throughout, so
        any sane scorer rejects them. They mostly confirm the pipeline is not
        matching on category alone.

        *Unstocked variants* are the hard ones: same brand, same model line, one
        trim different ("Aurora A1 Ultra 1TB" where we stock "Aurora A1 256GB").
        Latent-semantic similarity is near-perfect for these, which is exactly
        why the fuzzy re-rank and the ambiguity margin exist.

        Every candidate is checked against the real catalog before it is kept. A
        distractor that happens to name a product we *do* sell is not a hard
        negative, it is a mislabelled positive, and it would make the matcher
        look wrong for being right.
        """
        if not carried or comp.distractor_rate <= 0:
            return []

        catalog_keys = {_canonical(p.brand, p.name) for p in catalog}
        catalog_brands = {p.brand.casefold() for p in catalog}
        rival_brands = [b for b in _RIVAL_BRANDS if b.casefold() not in catalog_brands]
        rival_models = [m for m in _RIVAL_MODELS if m.casefold() not in catalog_brands]
        if not rival_brands or not rival_models:
            raise ValueError("Distractor vocabulary collides with the catalog's own brands.")

        wanted = round(len(carried) * comp.distractor_rate)
        out: list[dict[str, Any]] = []
        attempts = 0
        while len(out) < wanted and attempts < wanted * 10:
            attempts += 1
            base = carried[int(self.rng.integers(0, len(carried)))]
            hard = bool(self.rng.random() < 0.5)

            if hard:
                brand = base.brand
                name = f"{_model_stem(base.name)} {self.rng.choice(_UNSTOCKED_VARIANTS)}"
            else:
                brand = str(self.rng.choice(rival_brands))
                model = str(self.rng.choice(rival_models))
                name = f"{model} {_model_stem(base.name).split()[-1]}"

            if _canonical(brand, name) in catalog_keys:
                continue

            tag = f"{'VAR' if hard else 'ALT'}{len(out):02d}"
            external_id = f"{comp.slug.upper()}-X-{tag}"
            spec = replace(base, sku=external_id, name=name, brand=brand, gtin="")
            out.append(
                {
                    "competitor_slug": comp.slug,
                    "external_id": external_id,
                    "title": self._mangle_title(spec, comp),
                    "brand": "" if self.rng.random() < comp.title_noise * 0.4 else brand,
                    # A GTIN that is deliberately absent from our catalog: the
                    # exact-match shortcut must not fire on it.
                    "gtin": (
                        f"9{int(self.rng.integers(10**11, 10**12 - 1))}"
                        if self.rng.random() < comp.gtin_publish_rate
                        else None
                    ),
                    "description": f"{brand} {name} available at {comp.name}.",
                    "url": f"https://{comp.domain}/p/{external_id.lower()}",
                    # No correct answer exists. Matching this listing to anything
                    # is a false positive.
                    "true_product_sku": None,
                    "_base_price": base.launch_price * float(self.rng.uniform(0.8, 1.35)),
                }
            )
        return out

    def _mangle_title(self, spec: ProductSpec, comp: CompetitorSpec) -> str:
        """Produce a realistically noisy competitor product title."""
        rng = self.rng
        noise = comp.title_noise
        title = f"{spec.brand} {spec.name}"

        if rng.random() < noise * 0.45:
            title = spec.name  # brand dropped
        if rng.random() < noise * 0.5:
            title = title.replace("GB", " GB")
        if rng.random() < noise * 0.4:
            title = title.upper()
        if rng.random() < noise * 0.55:
            suffix = rng.choice(
                [
                    "- Unlocked",
                    "(Latest Model)",
                    "| Free Shipping",
                    "- Refurb Grade A",
                    "2-Year Warranty",
                ]
            )
            title = f"{title} {suffix}"
        if rng.random() < noise * 0.3:
            title = f"{comp.name} Exclusive: {title}"
        if rng.random() < noise * 0.25 and len(title) > 6:
            pos = int(rng.integers(1, len(title) - 1))
            title = title[:pos] + title[pos + 1 :]  # dropped character typo
        return " ".join(title.split())
