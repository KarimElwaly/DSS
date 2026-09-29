"""Declarative specification of the synthetic market.

Brands and model names here are **fictional** on purpose: the demo dataset must
not embed real trademarks or scraped third-party content.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

DEFAULT_SEED = 20260927


@dataclass(frozen=True)
class CategorySpec:
    name: str
    slug: str
    #: Elasticity drawn per product as ``N(elasticity_mean, elasticity_sd)``.
    elasticity_mean: float
    elasticity_sd: float = 0.25
    #: Day of year where demand peaks (e.g. 334 == 30 Nov, Black Friday).
    seasonal_peak_doy: int = 334
    seasonal_amplitude: float = 0.18
    min_margin_pct: float = 0.08


@dataclass(frozen=True)
class ProductSpec:
    sku: str
    name: str
    brand: str
    gtin: str
    category_slug: str
    base_cost: float
    launch_price: float
    base_demand: float
    description: str = ""
    #: SKUs that customers treat as substitutes -> positive cross elasticity.
    substitutes: tuple[str, ...] = ()
    substitute_cross_elasticity: float = 0.35
    #: Positive competitor cross elasticity: rivals raise price, we sell more.
    competitor_cross_elasticity: float = 0.55
    #: Where the *true* revenue-maximising price sits relative to the reference
    #: price. Drives the quadratic curvature term, so the optimizer has a known
    #: interior optimum to be validated against.
    revenue_optimum_offset: float = -0.10
    promo_lift: float = 0.22
    trend_per_year: float = 0.05
    noise_sigma: float = 0.16
    attributes: dict[str, str] = field(default_factory=dict)

    def curvature(self, elasticity: float) -> float:
        r"""Quadratic coefficient :math:`\gamma` of ``ln q = a + eps*x + gamma*x^2``.

        With :math:`x=\ln(p/p_{ref})`, the local elasticity is
        :math:`\varepsilon + 2\gamma x`, which equals ``elasticity`` exactly at the
        reference price. Revenue is maximised where local elasticity is ``-1``,
        i.e. at ``x* = ln(1 + revenue_optimum_offset)``; solving
        ``1 + eps + 2*gamma*x* = 0`` yields the coefficient below.
        """
        x_star = math.log(1.0 + self.revenue_optimum_offset)
        if abs(x_star) < 1e-9 or elasticity >= -1.0:
            return 0.0
        return -(1.0 + elasticity) / (2.0 * x_star)


@dataclass(frozen=True)
class CompetitorSpec:
    name: str
    slug: str
    domain: str
    #: Long-run price positioning versus our list price (1.0 == parity).
    price_index: float = 1.0
    #: How fast the rival closes the gap to our price each day, in [0, 1].
    reaction_speed: float = 0.15
    #: Daily probability of entering a stockout.
    stockout_rate: float = 0.010
    stockout_mean_days: float = 5.0
    #: Probability that this rival lists any given SKU at all.
    coverage: float = 0.85
    #: Probability of a promotional day.
    promo_rate: float = 0.06
    price_noise: float = 0.02
    #: How mangled their product titles are -- exercises the matching pipeline.
    title_noise: float = 0.5
    #: Share of listings that publish a GTIN (enables the exact-match shortcut).
    gtin_publish_rate: float = 0.45
    #: Listings for products we do NOT sell, as a fraction of the SKUs carried.
    #: Without these every listing has a correct answer, so the matcher cannot
    #: produce a false positive and its precision is unmeasurable. Real rivals
    #: carry a far wider range than we do, and the near-misses among them (a
    #: capacity or trim we never stocked) are the genuinely hard negatives.
    distractor_rate: float = 0.4


@dataclass(frozen=True)
class MarketConfig:
    organization_name: str = "Northwind Electronics"
    organization_slug: str = "northwind"
    currency: str = "USD"
    seed: int = DEFAULT_SEED
    #: Length of generated sales history.
    history_days: int = 730
    end_date: dt.date | None = None

    categories: tuple[CategorySpec, ...] = ()
    products: tuple[ProductSpec, ...] = ()
    competitors: tuple[CompetitorSpec, ...] = ()

    # -- price process ------------------------------------------------------
    promo_rate: float = 0.05
    promo_mean_days: float = 4.0
    promo_depth_range: tuple[float, float] = (0.08, 0.25)
    #: Probability per day of an exogenous supplier cost shock.
    cost_shock_rate: float = 0.008
    cost_shock_sd: float = 0.06
    #: Fraction of a cost change passed through to list price. Cost shocks are
    #: the instrument that makes elasticity identifiable.
    cost_pass_through: float = 0.65
    #: Deliberate small price experiments -- the main source of identification.
    price_walk_sd: float = 0.012
    price_walk_reversion: float = 0.08
    max_price_drift: float = 0.22

    def resolved_end_date(self) -> dt.date:
        return self.end_date or dt.date.today()

    def start_date(self) -> dt.date:
        return self.resolved_end_date() - dt.timedelta(days=self.history_days - 1)


_CATEGORIES: tuple[CategorySpec, ...] = (
    CategorySpec(
        "Smartphones", "smartphones", -2.20, 0.22, seasonal_peak_doy=334, seasonal_amplitude=0.20
    ),
    CategorySpec("Laptops", "laptops", -1.75, 0.20, seasonal_peak_doy=240, seasonal_amplitude=0.24),
    CategorySpec(
        "Headphones", "headphones", -2.60, 0.28, seasonal_peak_doy=350, seasonal_amplitude=0.30
    ),
    CategorySpec("Tablets", "tablets", -2.05, 0.22, seasonal_peak_doy=345, seasonal_amplitude=0.22),
    CategorySpec(
        "Smartwatches", "smartwatches", -2.40, 0.26, seasonal_peak_doy=352, seasonal_amplitude=0.26
    ),
)


def _p(
    sku: str,
    name: str,
    brand: str,
    gtin: str,
    category: str,
    cost: float,
    price: float,
    demand: float,
    substitutes: tuple[str, ...] = (),
    **kwargs: object,
) -> ProductSpec:
    return ProductSpec(
        sku=sku,
        name=name,
        brand=brand,
        gtin=gtin,
        category_slug=category,
        base_cost=cost,
        launch_price=price,
        base_demand=demand,
        substitutes=substitutes,
        description=f"{brand} {name}",
        **kwargs,  # type: ignore[arg-type]
    )


_PRODUCTS: tuple[ProductSpec, ...] = (
    # -- smartphones --------------------------------------------------------
    _p(
        "PHN-A1-128",
        "Aurora A1 128GB",
        "Aurora",
        "0750112000015",
        "smartphones",
        392.0,
        649.0,
        42.0,
        ("PHN-A1-256", "PHN-N5-128"),
    ),
    _p(
        "PHN-A1-256",
        "Aurora A1 256GB",
        "Aurora",
        "0750112000022",
        "smartphones",
        431.0,
        729.0,
        27.0,
        ("PHN-A1-128",),
    ),
    _p(
        "PHN-A1P-256",
        "Aurora A1 Pro 256GB",
        "Aurora",
        "0750112000039",
        "smartphones",
        612.0,
        1049.0,
        16.0,
        ("PHN-A1-256",),
    ),
    _p(
        "PHN-N5-128",
        "Nexon N5 128GB",
        "Nexon",
        "0750112000046",
        "smartphones",
        268.0,
        449.0,
        55.0,
        ("PHN-A1-128",),
    ),
    _p(
        "PHN-N5L-64",
        "Nexon N5 Lite 64GB",
        "Nexon",
        "0750112000053",
        "smartphones",
        171.0,
        289.0,
        78.0,
        ("PHN-N5-128",),
    ),
    # -- laptops ------------------------------------------------------------
    _p(
        "LAP-V14-512",
        "Volta Book 14 512GB",
        "Volta",
        "0750112000060",
        "laptops",
        648.0,
        1099.0,
        14.0,
        ("LAP-V14-1TB", "LAP-K15-512"),
    ),
    _p(
        "LAP-V14-1TB",
        "Volta Book 14 1TB",
        "Volta",
        "0750112000077",
        "laptops",
        731.0,
        1249.0,
        9.0,
        ("LAP-V14-512",),
    ),
    _p(
        "LAP-V16-1TB",
        "Volta Book 16 Studio 1TB",
        "Volta",
        "0750112000084",
        "laptops",
        1120.0,
        1899.0,
        5.0,
        ("LAP-V14-1TB",),
    ),
    _p(
        "LAP-K15-512",
        "Kestrel Air 15 512GB",
        "Kestrel",
        "0750112000091",
        "laptops",
        519.0,
        879.0,
        21.0,
        ("LAP-V14-512",),
    ),
    _p(
        "LAP-K13-256",
        "Kestrel Air 13 256GB",
        "Kestrel",
        "0750112000107",
        "laptops",
        388.0,
        649.0,
        26.0,
        ("LAP-K15-512",),
    ),
    # -- headphones ---------------------------------------------------------
    _p(
        "AUD-P70-BLK",
        "Pulse 70 ANC Over-Ear Black",
        "Pulse",
        "0750112000114",
        "headphones",
        82.0,
        179.0,
        96.0,
        ("AUD-P70-WHT", "AUD-P40-BLK"),
    ),
    _p(
        "AUD-P70-WHT",
        "Pulse 70 ANC Over-Ear White",
        "Pulse",
        "0750112000121",
        "headphones",
        82.0,
        179.0,
        61.0,
        ("AUD-P70-BLK",),
    ),
    _p(
        "AUD-P40-BLK",
        "Pulse 40 Wireless Black",
        "Pulse",
        "0750112000138",
        "headphones",
        34.0,
        89.0,
        148.0,
        ("AUD-P70-BLK",),
    ),
    _p(
        "AUD-E20-BUDS",
        "Echo 20 True Wireless Buds",
        "Echo",
        "0750112000145",
        "headphones",
        21.0,
        59.0,
        210.0,
        ("AUD-P40-BLK",),
    ),
    _p(
        "AUD-E50-BUDS",
        "Echo 50 ANC Buds",
        "Echo",
        "0750112000152",
        "headphones",
        48.0,
        129.0,
        88.0,
        ("AUD-E20-BUDS",),
    ),
    # -- tablets ------------------------------------------------------------
    _p(
        "TAB-S10-64",
        "Slate 10 64GB WiFi",
        "Slate",
        "0750112000169",
        "tablets",
        142.0,
        269.0,
        47.0,
        ("TAB-S10-128", "TAB-S11-128"),
    ),
    _p(
        "TAB-S10-128",
        "Slate 10 128GB WiFi",
        "Slate",
        "0750112000176",
        "tablets",
        168.0,
        319.0,
        31.0,
        ("TAB-S10-64",),
    ),
    _p(
        "TAB-S11-128",
        "Slate 11 Plus 128GB",
        "Slate",
        "0750112000183",
        "tablets",
        231.0,
        429.0,
        22.0,
        ("TAB-S10-128",),
    ),
    _p(
        "TAB-M08-32",
        "Mesa 8 32GB",
        "Mesa",
        "0750112000190",
        "tablets",
        71.0,
        139.0,
        74.0,
        ("TAB-S10-64",),
    ),
    # -- smartwatches -------------------------------------------------------
    _p(
        "WCH-O3-42",
        "Orbit 3 42mm",
        "Orbit",
        "0750112000206",
        "smartwatches",
        96.0,
        219.0,
        58.0,
        ("WCH-O3-46", "WCH-T2-40"),
    ),
    _p(
        "WCH-O3-46",
        "Orbit 3 46mm",
        "Orbit",
        "0750112000213",
        "smartwatches",
        108.0,
        249.0,
        39.0,
        ("WCH-O3-42",),
    ),
    _p(
        "WCH-O3S-46",
        "Orbit 3 Sport 46mm",
        "Orbit",
        "0750112000220",
        "smartwatches",
        118.0,
        279.0,
        24.0,
        ("WCH-O3-46",),
    ),
    _p(
        "WCH-T2-40",
        "Tempo 2 40mm",
        "Tempo",
        "0750112000237",
        "smartwatches",
        52.0,
        129.0,
        91.0,
        ("WCH-O3-42",),
    ),
    _p(
        "WCH-T2A-44",
        "Tempo 2 Active 44mm",
        "Tempo",
        "0750112000244",
        "smartwatches",
        64.0,
        159.0,
        66.0,
        ("WCH-T2-40",),
    ),
)


_COMPETITORS: tuple[CompetitorSpec, ...] = (
    CompetitorSpec(
        name="MegaMart Online",
        slug="megamart",
        domain="megamart.example",
        price_index=0.96,
        reaction_speed=0.28,
        stockout_rate=0.008,
        coverage=0.92,
        promo_rate=0.08,
        title_noise=0.35,
        gtin_publish_rate=0.70,
    ),
    CompetitorSpec(
        name="TechDepot",
        slug="techdepot",
        domain="techdepot.example",
        price_index=1.04,
        reaction_speed=0.12,
        stockout_rate=0.018,
        coverage=0.80,
        promo_rate=0.05,
        title_noise=0.65,
        gtin_publish_rate=0.30,
    ),
    CompetitorSpec(
        name="ValueBox",
        slug="valuebox",
        domain="valuebox.example",
        price_index=0.91,
        reaction_speed=0.20,
        stockout_rate=0.035,
        coverage=0.62,
        promo_rate=0.11,
        title_noise=0.85,
        gtin_publish_rate=0.10,
    ),
)


def default_electronics_market(
    *,
    seed: int = DEFAULT_SEED,
    history_days: int = 730,
    end_date: dt.date | None = None,
) -> MarketConfig:
    """The demo market used by ``dss seed`` and by the validation experiments."""
    return MarketConfig(
        seed=seed,
        history_days=history_days,
        end_date=end_date,
        categories=_CATEGORIES,
        products=_PRODUCTS,
        competitors=_COMPETITORS,
    )
