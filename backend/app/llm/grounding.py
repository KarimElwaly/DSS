"""Numeric-grounding validator for LLM advisor outputs (Module F).

Asserts that every numeric token (dollar amounts, percentages, unit counts, ratios)
in the generated CFO advisor narrative is directly traceable to the structured
facts JSON payload, preventing hallucinations.
"""

from __future__ import annotations

import re
from typing import Any


def extract_numbers_from_facts(facts: Any) -> set[float]:
    """Recursively extract all numeric values from the facts dictionary."""
    numbers: set[float] = set()

    def _collect(val: Any) -> None:
        if isinstance(val, (int, float)):
            if not isinstance(val, bool):
                v = float(val)
                numbers.add(round(v, 2))
                numbers.add(round(v, 1))
                numbers.add(round(v, 4))
                # Add percentage representation (e.g. 0.295 -> 29.5, 30)
                if abs(v) <= 1.0:
                    pct = v * 100.0
                    numbers.add(round(pct, 2))
                    numbers.add(round(pct, 1))
                    numbers.add(round(pct, 0))
                # Add million/thousand representation
                if abs(v) >= 1_000_000:
                    numbers.add(round(v / 1_000_000, 2))
                    numbers.add(round(v / 1_000_000, 1))
                elif abs(v) >= 1_000:
                    numbers.add(round(v / 1_000, 2))
                    numbers.add(round(v / 1_000, 1))
        elif isinstance(val, dict):
            for v in val.values():
                _collect(v)
        elif isinstance(val, (list, tuple, set)):
            for item in val:
                _collect(item)

    _collect(facts)
    return numbers


def extract_numbers_from_text(text: str) -> list[tuple[str, float]]:
    """Extract numeric tokens from free-form text.

    Returns:
        List of (raw_token_str, parsed_float_value).
    """
    # Regex to capture currency ($1.2M, $450k, $29.99), percentages (18.5%), and raw numbers
    pattern = re.compile(
        r"(?:\$)?(?:\b|-|\+)(\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?(?:k|m|b)?%?",
        re.IGNORECASE,
    )

    extracted: list[tuple[str, float]] = []

    # Exclude common dates/years (2020-2035), quarters, or bullet numbers
    date_years = {float(y) for y in range(2020, 2035)}
    ignored_integers = {1.0, 2.0, 3.0, 4.0, 5.0, 7.0, 14.0, 30.0, 90.0, 365.0}

    for match in pattern.finditer(text):
        raw = match.group().strip()
        cleaned = raw.replace("$", "").replace(",", "").strip()

        is_pct = cleaned.endswith("%")
        if is_pct:
            cleaned = cleaned[:-1]

        mult = 1.0
        if cleaned.lower().endswith("m"):
            mult = 1_000_000.0
            cleaned = cleaned[:-1]
        elif cleaned.lower().endswith("k"):
            mult = 1_000.0
            cleaned = cleaned[:-1]
        elif cleaned.lower().endswith("b"):
            mult = 1_000_000_000.0
            cleaned = cleaned[:-1]

        try:
            val = float(cleaned) * mult
        except ValueError:
            continue

        # Skip calendar years and common list numbering
        if val in date_years or (val in ignored_integers and not is_pct and not raw.startswith("$")):
            continue

        extracted.append((raw, val))

    return extracted


def validate_numeric_grounding(
    narrative: str, facts: dict[str, Any], *, tolerance_pct: float = 0.03
) -> tuple[bool, list[str]]:
    """Verify that every numeric claim in the narrative matches a fact in the payload.

    Returns:
        (passed: bool, violations: list[str])
    """
    known_facts = extract_numbers_from_facts(facts)
    tokens = extract_numbers_from_text(narrative)

    violations: list[str] = []

    for raw, val in tokens:
        # Check if val matches any number in known_facts within tolerance
        matched = False
        for fact_val in known_facts:
            diff = abs(val - fact_val)
            denom = max(abs(fact_val), 1.0)
            if (diff / denom) <= tolerance_pct or diff < 0.15:
                matched = True
                break

        if not matched:
            violations.append(
                f"Numeric token '{raw}' (value={val}) is not grounded in the facts JSON payload."
            )

    passed = len(violations) == 0
    return passed, violations
