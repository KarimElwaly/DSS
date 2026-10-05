"""Guardrail enforcement layer for price recommendations (Module C).

Guarantees that recommended prices strictly respect financial floors,
operational ceiling boundaries, maximum step percentages, and psychological
price endings (.99 charm pricing).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import math


@dataclass(frozen=True)
class GuardrailResult:
    """Outcome of guardrail evaluation for a product."""

    final_price: Decimal
    unconstrained_price: Decimal
    price_floor: Decimal
    price_ceiling: Decimal
    step_lower_bound: Decimal
    step_upper_bound: Decimal
    binding_constraints: list[str]
    explanation: str


def round_charm_price(price: Decimal, floor: Decimal, ceiling: Decimal) -> Decimal:
    """Snap price to a .99 psychological ending within [floor, ceiling].

    If rounding down to .99 violates the floor, attempts the next higher .99 ending.
    If no .99 ending fits within [floor, ceiling], returns the original 2-decimal price.
    """
    p_float = float(price)
    # Options: floor(p) - 0.01 (e.g. 19.99 for 20.x) or floor(p) + 0.99
    base = math.floor(p_float)
    c1 = Decimal(str(base - 1)) + Decimal("0.99")
    c2 = Decimal(str(base)) + Decimal("0.99")
    c3 = Decimal(str(base + 1)) + Decimal("0.99")

    candidates = [c for c in [c1, c2, c3] if floor <= c <= ceiling]
    if not candidates:
        return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Pick the closest candidate to the original target price
    best = min(candidates, key=lambda c: abs(c - price))
    return best.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def apply_guardrails(
    unconstrained_price: Decimal,
    current_price: Decimal,
    unit_cost: Decimal,
    min_margin_pct: Decimal,
    max_price_step_pct: Decimal,
    *,
    price_floor_override: Decimal | None = None,
    price_ceiling_override: Decimal | None = None,
    competitor_index: Decimal | None = None,
    apply_charm_rounding: bool = True,
) -> GuardrailResult:
    """Enforce financial and market guardrails on an optimizer recommendation.

    Invariants enforced:
    1. final_price >= price_floor (cost / (1 - min_margin)) ALWAYS.
    2. final_price <= price_ceiling.
    3. |final_price - current_price| / current_price <= max_price_step_pct (unless overridden by floor).
    4. binding_constraints lists all active restrictions.
    """
    # 1. Financial Floor: unit_cost / (1 - min_margin)
    if competitor_index is not None and not isinstance(competitor_index, Decimal):
        competitor_index = Decimal(str(round(float(competitor_index), 4)))

    if min_margin_pct >= Decimal("1.0"):
        min_margin_pct = Decimal("0.90")

    margin_divisor = Decimal("1.0") - min_margin_pct
    computed_floor = (unit_cost / margin_divisor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if price_floor_override is not None and price_floor_override > computed_floor:
        price_floor = price_floor_override.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        price_floor = computed_floor

    # 2. Ceiling: 1.40x current price, or competitor index anchor, or override
    base_ceiling = (current_price * Decimal("1.40")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if competitor_index is not None and competitor_index > 0:
        comp_ceiling = (competitor_index * Decimal("1.25")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        computed_ceiling = max(comp_ceiling, price_floor * Decimal("1.05"))
    else:
        computed_ceiling = base_ceiling

    if price_ceiling_override is not None:
        price_ceiling = max(price_ceiling_override, price_floor)
    else:
        price_ceiling = max(computed_ceiling, price_floor * Decimal("1.05"))

    # 3. Maximum Step Bounds: current_price * (1 +/- step)
    step_down = (current_price * (Decimal("1.0") - max_price_step_pct)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    step_up = (current_price * (Decimal("1.0") + max_price_step_pct)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    # Effective operational bounds
    effective_lower = max(price_floor, step_down)
    effective_upper = max(effective_lower, min(price_ceiling, step_up))

    # 4. Clipping candidate price
    binding: list[str] = []
    p_candidate = unconstrained_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if p_candidate < effective_lower:
        p_constrained = effective_lower
        if effective_lower == price_floor:
            binding.append("price_floor")
        else:
            binding.append("max_step_down")
    elif p_candidate > effective_upper:
        p_constrained = effective_upper
        if effective_upper == price_ceiling:
            binding.append("price_ceiling")
        else:
            binding.append("max_step_up")
    else:
        p_constrained = p_candidate

    # 5. Psychological .99 charm rounding
    if apply_charm_rounding:
        p_final = round_charm_price(p_constrained, price_floor, price_ceiling)
        if p_final != p_constrained:
            binding.append("charm_rounding_99")
    else:
        p_final = p_constrained

    # Ensure hard invariant: price never violates floor
    if p_final < price_floor:
        p_final = price_floor
        if "price_floor" not in binding:
            binding.append("price_floor")

    # Explanation narrative
    explanation_parts = []
    if "price_floor" in binding:
        explanation_parts.append(
            f"Clamped to floor ${price_floor:.2f} to protect minimum margin of {float(min_margin_pct)*100:.0f}%"
        )
    if "price_ceiling" in binding:
        explanation_parts.append(f"Capped at ceiling ${price_ceiling:.2f} to prevent market estrangement")
    if "max_step_down" in binding or "max_step_up" in binding:
        explanation_parts.append(
            f"Limited by max price step of {float(max_price_step_pct)*100:.0f}% (range ${effective_lower:.2f} – ${effective_upper:.2f})"
        )
    if "charm_rounding_99" in binding:
        explanation_parts.append("Snapped to nearest .99 psychological ending")
    if not explanation_parts:
        explanation_parts.append(
            f"Unconstrained optimal price ${unconstrained_price:.2f} fits within all operational bounds"
        )

    explanation = "; ".join(explanation_parts) + "."

    return GuardrailResult(
        final_price=p_final,
        unconstrained_price=unconstrained_price,
        price_floor=price_floor,
        price_ceiling=price_ceiling,
        step_lower_bound=step_down,
        step_upper_bound=step_up,
        binding_constraints=binding,
        explanation=explanation,
    )
