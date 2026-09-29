"""Select the auto-accept threshold across several independent markets.

    python scripts/tune_match_threshold.py --seeds 11,42,99,2026,777

Why this exists as a separate script from ``check_matching.py``: a sweep run on
a *single* market is misleading. On the default seed the curve suggests 0.78 is
safe, because that market happens to contain no confusable pair near the
boundary. Pooled over five seeds, 0.78 produces false positives and only 0.86
holds precision at 100%.

Selecting a threshold on one sample of the data it will be judged on is the
classic way to report a number that does not survive contact with new data.
Each seed here is an independently generated market with its own catalog
coverage, title noise and distractors, so the pooled figure is the one worth
trusting.

The script never commits: each market is built inside a transaction that is
rolled back, so the developer's seeded data is untouched.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from dataclasses import dataclass

from sqlalchemy import select

from app.db import get_engine, get_session_factory
from app.models.enums import MatchMethod
from app.models.market import CompetitorListing
from app.services.matching import run_matching
from app.services.seed import get_default_organization, seed_market
from simulator import MarketSimulator, default_electronics_market

DEFAULT_SEEDS = (11, 42, 99, 2026, 777)
DEFAULT_THRESHOLDS = (0.70, 0.74, 0.78, 0.80, 0.82, 0.84, 0.86, 0.90)


@dataclass
class Tally:
    accepted: int = 0
    false_positives: int = 0
    resolved: int = 0
    total: int = 0

    @property
    def precision(self) -> float:
        if not self.accepted:
            return 0.0
        return (self.accepted - self.false_positives) / self.accepted

    @property
    def recall(self) -> float:
        return self.resolved / self.total if self.total else 0.0


def _score_one_market(seed: int, history_days: int) -> list[tuple[str | None, list[dict]]]:
    """Build a market, match it, and return (truth, candidates) per listing."""
    connection = get_engine().connect()
    transaction = connection.begin()
    session = get_session_factory()(bind=connection)
    try:
        config = default_electronics_market(
            seed=seed, history_days=history_days, end_date=dt.date.today()
        )
        seed_market(session, MarketSimulator(config).generate(), reset=True)
        run_matching(session, get_default_organization(session).id)

        rows = [
            (
                listing.match_explanation.get("ground_truth_sku"),
                listing.match_explanation.get("candidates") or [],
            )
            for listing in session.scalars(select(CompetitorListing))
            # GTIN hits never consult the threshold, so including them would
            # only dilute the measurement with guaranteed successes.
            if listing.match_method in (MatchMethod.EMBEDDING, MatchMethod.UNMATCHED)
        ]
        return [(truth, candidates) for truth, candidates in rows if candidates]
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _tally(rows: list[tuple[str | None, list[dict]]], threshold: float) -> Tally:
    result = Tally(total=len(rows))
    for truth, candidates in rows:
        best = candidates[0]
        if best["score"] >= threshold:
            result.accepted += 1
            if best["sku"] == truth:
                result.resolved += 1
            else:
                result.false_positives += 1
        elif truth is None:
            result.resolved += 1  # correctly withheld from the price index
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seeds",
        default=",".join(str(s) for s in DEFAULT_SEEDS),
        help="Comma-separated market seeds.",
    )
    parser.add_argument("--history-days", type=int, default=60)
    args = parser.parse_args()

    logging.disable(logging.INFO)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    per_seed: dict[int, list[tuple[str | None, list[dict]]]] = {}
    for seed in seeds:
        per_seed[seed] = _score_one_market(seed, args.history_days)
        print(f"  market seed={seed}: {len(per_seed[seed])} listings on the embedding path")

    print("\nPer-seed false positives (a single market is not enough to decide):")
    header = "  " + "".join(f"{t:>8.2f}" for t in DEFAULT_THRESHOLDS)
    print(f"  {'seed':<6}" + header)
    for seed, rows in per_seed.items():
        cells = "".join(f"{_tally(rows, t).false_positives:>8}" for t in DEFAULT_THRESHOLDS)
        print(f"  {seed:<6}  {cells}")

    print("\nPooled across every market:")
    head = f"{'threshold':>10} {'accepted':>9} {'false pos':>10} {'precision':>10} {'recall':>8}"
    print(head)
    print("-" * len(head))

    safest: float | None = None
    for threshold in DEFAULT_THRESHOLDS:
        pooled = Tally()
        for rows in per_seed.values():
            one = _tally(rows, threshold)
            pooled.accepted += one.accepted
            pooled.false_positives += one.false_positives
            pooled.resolved += one.resolved
            pooled.total += one.total
        if pooled.false_positives == 0 and safest is None:
            safest = threshold
        print(
            f"{threshold:>10.2f} {pooled.accepted:>9} {pooled.false_positives:>10} "
            f"{pooled.precision:>10.1%} {pooled.recall:>8.1%}"
        )

    if safest is None:
        print("\nNo threshold in the sweep reached 100% precision. Widen the range.")
        return 1

    print(
        f"\nLowest threshold with zero false positives across all {len(seeds)} markets: "
        f"{safest:.2f}\nSet DSS_MATCH_AUTO_ACCEPT to this value; anything lower buys recall "
        "with a corrupted price index."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
