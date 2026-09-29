"""Measure matching accuracy against the simulator's ground truth.

Run against a seeded database::

    python scripts/check_matching.py

Two things make this an honest measurement rather than a demo:

**GTIN hits are reported separately.** A published barcode is an exact
identifier, so those matches are free. Folding them into one blended accuracy
would mostly measure the simulator's ``gtin_publish_rate``.

**Distractor listings count.** Competitors list products we do not sell, and
the simulator generates them deliberately. Without negatives every listing has
a correct answer, precision is 100% by construction, and the auto-accept
threshold is unfalsifiable.

A wrong match is treated as worse than no match. An unmatched listing leaves a
gap in the competitor price index; a confidently wrong one poisons it, and the
error propagates into every elasticity estimate and price recommendation built
on top.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select

from app.db import session_scope
from app.models.catalog import Product
from app.models.enums import MatchMethod, MatchStatus
from app.models.market import CompetitorListing
from app.services.matching import run_matching
from app.services.seed import get_default_organization

ACCEPTED = (MatchStatus.AUTO_ACCEPTED, MatchStatus.CONFIRMED)


@dataclass
class Scores:
    label: str
    total: int = 0
    accepted: int = 0
    resolved_correctly: int = 0
    false_positives: int = 0
    deferred: int = 0
    unmatched: int = 0

    @property
    def precision(self) -> float:
        """Of the matches we acted on, how many were right."""
        if not self.accepted:
            return 0.0
        return (self.accepted - self.false_positives) / self.accepted

    @property
    def recall(self) -> float:
        """Of all listings, how many ended in the right state without a human.

        Correctly declining to match a product we do not sell counts as a
        success here: withholding it from the price index is the desired
        outcome, not a miss.
        """
        return self.resolved_correctly / self.total if self.total else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def row(self) -> str:
        return (
            f"{self.label:<24} {self.total:>6} {self.accepted:>9} "
            f"{self.precision:>10.1%} {self.recall:>8.1%} {self.f1:>7.1%} "
            f"{self.false_positives:>4} {self.deferred:>8} {self.unmatched:>10}"
        )


def main() -> int:
    with session_scope() as session:
        org = get_default_organization(session)

        report = run_matching(session, org.id)
        print("Run summary:", report.as_dict(), "\n")

        sku_by_id = dict(
            session.execute(
                select(Product.id, Product.sku).where(Product.organization_id == org.id)
            ).all()
        )
        listings = list(
            session.scalars(
                select(CompetitorListing).where(CompetitorListing.organization_id == org.id)
            )
        )

        overall = Scores("overall")
        real = Scores("products we sell")
        distractors = Scores("products we don't sell")
        by_method: dict[MatchMethod, Scores] = {}
        errors: list[tuple[str, str, str, float]] = []

        for listing in listings:
            truth = listing.match_explanation.get("ground_truth_sku")
            bucket = by_method.setdefault(
                listing.match_method, Scores(f"  via {listing.match_method.value}")
            )
            targets = (overall, real if truth else distractors, bucket)
            for scores in targets:
                scores.total += 1

            acted = listing.match_status in ACCEPTED and listing.matched_product_id is not None
            if acted:
                predicted = sku_by_id.get(listing.matched_product_id, "?")
                correct = predicted == truth
                for scores in targets:
                    scores.accepted += 1
                    scores.resolved_correctly += int(correct)
                    scores.false_positives += int(not correct)
                if not correct:
                    errors.append(
                        (
                            listing.title,
                            truth or "<not in our catalog>",
                            predicted,
                            listing.match_confidence or 0.0,
                        )
                    )
            elif listing.match_status == MatchStatus.NEEDS_REVIEW:
                for scores in targets:
                    scores.deferred += 1
            else:
                for scores in targets:
                    scores.unmatched += 1
                    scores.resolved_correctly += int(truth is None)

        header = (
            f"{'segment':<24} {'total':>6} {'accepted':>9} "
            f"{'precision':>10} {'recall':>8} {'f1':>7} {'FP':>4} {'review':>8} {'unmatched':>10}"
        )
        print(header)
        print("-" * len(header))
        print(real.row())
        for method in (MatchMethod.GTIN, MatchMethod.EMBEDDING, MatchMethod.UNMATCHED):
            scores = by_method.get(method)
            if scores and scores.total:
                print(scores.row())
        print(distractors.row())
        print("-" * len(header))
        print(overall.row())

        withheld = distractors.unmatched + distractors.deferred
        print(
            f"\nHeadline: {overall.false_positives} false positive(s) across "
            f"{overall.total} listings. Of the {distractors.total} listings for products "
            f"we do not sell, {withheld} were correctly kept out of the price index."
        )

        if errors:
            print(f"\n{len(errors)} false positive(s):")
            for title, truth, predicted, confidence in errors[:10]:
                print(f"  [{confidence:.3f}] {title!r}\n      truth={truth} predicted={predicted}")
        else:
            print("\nNo false positives: every accepted match was correct.")

        review_reasons = Counter(
            str(row.match_explanation.get("reason", "")).split(":")[0]
            for row in listings
            if row.match_status == MatchStatus.NEEDS_REVIEW
        )
        if review_reasons:
            print("\nWhy listings were deferred to review:")
            for reason, count in review_reasons.most_common():
                print(f"  {count:>4}  {reason}")

        _threshold_sweep(listings)

        session.rollback()  # measurement only; leave the database untouched
    return 0


def _threshold_sweep(listings: list[CompetitorListing]) -> None:
    """Show the precision/recall trade-off on *this* market.

    Re-scored from the candidate lists already persisted in
    ``match_explanation``, so no re-matching is needed. GTIN hits are excluded:
    they never consult the threshold.

    Read this as a diagnostic, not as a way to choose the threshold. A single
    market is an easy sample -- it may simply contain no confusable pair near
    the boundary, which makes a low threshold look safe. Use
    ``scripts/tune_match_threshold.py``, which pools several independently
    generated markets, to actually set the value.
    """
    rows = [
        (
            row.match_explanation.get("ground_truth_sku"),
            row.match_explanation.get("candidates") or [],
        )
        for row in listings
        if row.match_method in (MatchMethod.EMBEDDING, MatchMethod.UNMATCHED)
    ]
    rows = [(truth, candidates) for truth, candidates in rows if candidates]
    if not rows:
        return

    print("\nAuto-accept threshold sweep (embedding path only):")
    header = f"{'threshold':>10} {'accepted':>9} {'false pos':>10} {'precision':>10} {'recall':>8}"
    print(header)
    print("-" * len(header))
    for step in range(11):
        threshold = round(0.50 + 0.04 * step, 2)
        accepted = false_positives = resolved = 0
        for truth, candidates in rows:
            best = candidates[0]
            if best["score"] >= threshold:
                accepted += 1
                if best["sku"] == truth:
                    resolved += 1
                else:
                    false_positives += 1
            elif truth is None:
                resolved += 1  # correctly withheld
        precision = (accepted - false_positives) / accepted if accepted else 0.0
        print(
            f"{threshold:>10.2f} {accepted:>9} {false_positives:>10} "
            f"{precision:>10.1%} {resolved / len(rows):>8.1%}"
        )


if __name__ == "__main__":
    sys.exit(main())
