"""Competitor listing -> internal SKU matching (Module A, step 2).

The pipeline is deliberately staged cheapest-first:

1. **GTIN exact.** A published barcode is an identifier, not a guess. Accepted
   outright at confidence 1.0.
2. **Vector recall.** Embed the listing text and pull the ``k`` nearest catalog
   products from pgvector. This is recall-oriented: it must not miss the right
   product, it is allowed to return wrong ones.
3. **Fuzzy re-rank.** RapidFuzz ``token_set_ratio`` over normalised text, which
   is precision-oriented: it punishes the token differences ("128 gb" versus
   "256 gb") that latent semantics smooths over.
4. **Decide.** Blend the two scores, then apply an absolute threshold *and* an
   ambiguity margin.

The ambiguity margin matters more than the absolute threshold. A wrong match is
worse than no match: an unmatched listing merely leaves a gap in the price
index, whereas a confident wrong match silently poisons it, and every
downstream elasticity estimate and price recommendation inherits the error.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.catalog import Product
from app.models.enums import MatchMethod, MatchStatus, ModelKind
from app.models.market import CompetitorListing, MatchCandidate
from app.models.pricing import ModelRun
from app.services.matching.embedder import Embedder, TfidfSvdEmbedder, artifact_path, build_embedder
from app.services.matching.text import normalize

logger = get_logger(__name__)

#: Statuses a human has settled. Re-running the matcher must never overwrite them.
_HUMAN_DECIDED = (MatchStatus.CONFIRMED, MatchStatus.REJECTED)


@dataclass
class Candidate:
    product_id: uuid.UUID
    sku: str
    vector_score: float
    fuzzy_score: float
    brand_score: float
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "score": round(self.score, 4),
            "vector_score": round(self.vector_score, 4),
            "fuzzy_score": round(self.fuzzy_score, 4),
            "brand_score": round(self.brand_score, 4),
        }


@dataclass
class MatchOutcome:
    listing_id: uuid.UUID
    method: MatchMethod
    status: MatchStatus
    product_id: uuid.UUID | None
    confidence: float | None
    candidates: list[Candidate] = field(default_factory=list)
    reason: str = ""


@dataclass
class MatchReport:
    """Counts only -- accuracy is measured separately, against ground truth."""

    considered: int = 0
    gtin: int = 0
    auto_accepted: int = 0
    needs_review: int = 0
    unmatched: int = 0
    skipped_human_decided: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "considered": self.considered,
            "gtin": self.gtin,
            "auto_accepted": self.auto_accepted,
            "needs_review": self.needs_review,
            "unmatched": self.unmatched,
            "skipped_human_decided": self.skipped_human_decided,
        }


def _blend(vector_score: float, fuzzy_score: float, brand_score: float, weight: float) -> float:
    """Combine the recall and precision signals into one 0..1 score.

    Brand agreement acts as a multiplier rather than a third additive term: a
    confident text match on the wrong manufacturer is not a partial success,
    it is a different product.
    """
    base = weight * vector_score + (1.0 - weight) * fuzzy_score
    return base * (0.75 + 0.25 * brand_score)


def _fuzzy_score(listing_text: str, product_text: str) -> float:
    """Token similarity that is robust to boilerplate but not to extra specs.

    ``token_set_ratio`` scores a *superset* as a perfect match, which is what we
    want for "Aurora A1 128GB | Free Shipping" -- the retail padding should not
    count against it. But the same property makes "Volta Book 14 Plus 512GB"
    score 100% against "Volta Book 14 512GB", and those are different products.

    ``token_sort_ratio`` is length-sensitive and penalises exactly those extra
    tokens, while being far too harsh on boilerplate. Averaging the two keeps
    the tolerance of the first and the discrimination of the second.
    """
    containment = fuzz.token_set_ratio(listing_text, product_text)
    equality = fuzz.token_sort_ratio(listing_text, product_text)
    return (containment + equality) / 200.0


def _brand_score(listing_brand: str, product_brand: str, listing_title: str) -> float:
    """1.0 when the brands agree, 0.0 when they clearly differ."""
    product = normalize(product_brand)
    if not product:
        return 1.0  # nothing to contradict
    listing = normalize(listing_brand)
    if not listing:
        # Competitors frequently drop the brand field but keep it in the title.
        return 1.0 if product in normalize(listing_title) else 0.5
    return float(fuzz.token_set_ratio(listing, product) / 100.0)


class ListingMatcher:
    """Stateful matcher: fit once on the catalog, then match many listings."""

    def __init__(self, session: Session, embedder: Embedder | None = None) -> None:
        self.session = session
        self.settings = get_settings()
        self.embedder = embedder or build_embedder()

    # -- index side ---------------------------------------------------------

    def fit_catalog(self, organization_id: uuid.UUID) -> int:
        """Fit the embedder and write ``product.embedding`` for the whole catalog.

        The corpus deliberately includes competitor titles as well as our own
        product text. TF-IDF learns its vocabulary from what it is shown, and
        listing titles carry spellings and abbreviations the catalog never uses.
        """
        products = list(
            self.session.scalars(
                select(Product)
                .where(Product.organization_id == organization_id)
                .order_by(Product.sku)
            )
        )
        if not products:
            return 0

        listing_texts = list(
            self.session.scalars(
                select(CompetitorListing.title).where(
                    CompetitorListing.organization_id == organization_id
                )
            )
        )
        product_texts = [p.search_text for p in products]
        self.embedder.fit([*product_texts, *listing_texts])

        vectors = self.embedder.encode(product_texts)
        for product, vector in zip(products, vectors, strict=True):
            product.embedding = vector.tolist()
        self.session.flush()

        if isinstance(self.embedder, TfidfSvdEmbedder):
            self.embedder.save(artifact_path(self.embedder.name))

        logger.info("Embedded %d products with %s", len(products), self.embedder.name)
        return len(products)

    # -- query side ---------------------------------------------------------

    def _gtin_index(self, organization_id: uuid.UUID) -> dict[str, tuple[uuid.UUID, str]]:
        rows = self.session.execute(
            select(Product.gtin, Product.id, Product.sku).where(
                Product.organization_id == organization_id, Product.gtin.is_not(None)
            )
        )
        return {str(gtin): (pid, sku) for gtin, pid, sku in rows}

    def _nearest(
        self, organization_id: uuid.UUID, vector: Sequence[float], k: int
    ) -> list[tuple[Product, float]]:
        """Top-k catalog products by cosine distance, evaluated in PostgreSQL."""
        distance = Product.embedding.cosine_distance(list(vector))
        rows = self.session.execute(
            select(Product, distance.label("distance"))
            .where(Product.organization_id == organization_id, Product.embedding.is_not(None))
            .order_by(distance)
            .limit(k)
        )
        # pgvector cosine distance is 1 - cosine similarity over [0, 2]; both
        # operands are L2-normalised, so clamping to [0, 1] only guards float noise.
        return [(product, max(0.0, min(1.0, 1.0 - float(dist)))) for product, dist in rows]

    def _score_candidates(
        self, listing: CompetitorListing, neighbours: list[tuple[Product, float]]
    ) -> list[Candidate]:
        listing_text = normalize(listing.search_text)
        weight = self.settings.match_vector_weight

        candidates: list[Candidate] = []
        for product, vector_score in neighbours:
            fuzzy = _fuzzy_score(listing_text, normalize(product.search_text))
            brand = _brand_score(listing.brand, product.brand, listing.title)
            candidates.append(
                Candidate(
                    product_id=product.id,
                    sku=product.sku,
                    vector_score=vector_score,
                    fuzzy_score=fuzzy,
                    brand_score=brand,
                    score=_blend(vector_score, fuzzy, brand, weight),
                )
            )
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    def _decide(self, listing: CompetitorListing, candidates: list[Candidate]) -> MatchOutcome:
        if not candidates:
            return MatchOutcome(
                listing_id=listing.id,
                method=MatchMethod.UNMATCHED,
                status=MatchStatus.PENDING,
                product_id=None,
                confidence=None,
                reason="no catalog neighbours",
            )

        best = candidates[0]
        runner_up = candidates[1].score if len(candidates) > 1 else 0.0
        margin = best.score - runner_up

        if best.score < self.settings.match_review_floor:
            return MatchOutcome(
                listing_id=listing.id,
                method=MatchMethod.UNMATCHED,
                status=MatchStatus.PENDING,
                product_id=None,
                confidence=best.score,
                candidates=candidates,
                reason=f"best score {best.score:.3f} below review floor",
            )

        if best.score >= self.settings.match_auto_accept:
            if margin < self.settings.match_ambiguity_margin:
                return MatchOutcome(
                    listing_id=listing.id,
                    method=MatchMethod.EMBEDDING,
                    status=MatchStatus.NEEDS_REVIEW,
                    product_id=None,
                    confidence=best.score,
                    candidates=candidates,
                    reason=(
                        f"ambiguous: {best.sku} and {candidates[1].sku} differ by only {margin:.3f}"
                    ),
                )
            return MatchOutcome(
                listing_id=listing.id,
                method=MatchMethod.EMBEDDING,
                status=MatchStatus.AUTO_ACCEPTED,
                product_id=best.product_id,
                confidence=best.score,
                candidates=candidates,
                reason=f"score {best.score:.3f} with margin {margin:.3f}",
            )

        return MatchOutcome(
            listing_id=listing.id,
            method=MatchMethod.EMBEDDING,
            status=MatchStatus.NEEDS_REVIEW,
            product_id=None,
            confidence=best.score,
            candidates=candidates,
            reason=f"score {best.score:.3f} needs human confirmation",
        )

    def _apply(self, listing: CompetitorListing, outcome: MatchOutcome) -> None:
        listing.matched_product_id = outcome.product_id
        listing.match_confidence = outcome.confidence
        listing.match_method = outcome.method
        listing.match_status = outcome.status

        # `ground_truth_sku` is planted by the seeder for scoring only. Preserve
        # it verbatim: the matcher must never read it, but it must not destroy it.
        explanation = dict(listing.match_explanation or {})
        explanation.update(
            {
                "embedder": getattr(self.embedder, "name", "unknown"),
                "reason": outcome.reason,
                "candidates": [c.as_dict() for c in outcome.candidates[:5]],
            }
        )
        listing.match_explanation = explanation

        self.session.execute(delete(MatchCandidate).where(MatchCandidate.listing_id == listing.id))
        for rank, candidate in enumerate(outcome.candidates[: self.settings.match_top_k], start=1):
            self.session.add(
                MatchCandidate(
                    listing_id=listing.id,
                    product_id=candidate.product_id,
                    score=candidate.score,
                    vector_score=candidate.vector_score,
                    fuzzy_score=candidate.fuzzy_score,
                    rank=rank,
                )
            )

    def match_listings(
        self,
        organization_id: uuid.UUID,
        listings: Sequence[CompetitorListing],
        *,
        store_listing_embedding: bool = True,
    ) -> tuple[list[MatchOutcome], MatchReport]:
        report = MatchReport()
        gtin_index = self._gtin_index(organization_id)
        pending = [row for row in listings if row.match_status not in _HUMAN_DECIDED]
        report.skipped_human_decided = len(listings) - len(pending)
        if not pending:
            return [], report

        vectors = self.embedder.encode([row.search_text for row in pending])
        outcomes: list[MatchOutcome] = []

        for listing, vector in zip(pending, vectors, strict=True):
            report.considered += 1
            if store_listing_embedding:
                listing.embedding = vector.tolist()

            hit = gtin_index.get(listing.gtin) if listing.gtin else None
            if hit is not None:
                product_id, sku = hit
                outcome = MatchOutcome(
                    listing_id=listing.id,
                    method=MatchMethod.GTIN,
                    status=MatchStatus.AUTO_ACCEPTED,
                    product_id=product_id,
                    confidence=1.0,
                    reason=f"exact GTIN {listing.gtin} -> {sku}",
                )
                report.gtin += 1
            else:
                candidates = self._score_candidates(
                    listing, self._nearest(organization_id, vector, self.settings.match_top_k)
                )
                outcome = self._decide(listing, candidates)
                if outcome.status == MatchStatus.AUTO_ACCEPTED:
                    report.auto_accepted += 1
                elif outcome.status == MatchStatus.NEEDS_REVIEW:
                    report.needs_review += 1
                else:
                    report.unmatched += 1

            self._apply(listing, outcome)
            outcomes.append(outcome)

        self.session.flush()
        return outcomes, report


def run_matching(
    session: Session,
    organization_id: uuid.UUID,
    *,
    only_unmatched: bool = False,
    embedder: Embedder | None = None,
) -> MatchReport:
    """Fit the catalog index and match listings. Returns a summary report."""
    matcher = ListingMatcher(session, embedder=embedder)
    matcher.fit_catalog(organization_id)

    stmt = select(CompetitorListing).where(CompetitorListing.organization_id == organization_id)
    if only_unmatched:
        stmt = stmt.where(CompetitorListing.matched_product_id.is_(None))
    listings = list(session.scalars(stmt.order_by(CompetitorListing.id)))

    _, report = matcher.match_listings(organization_id, listings)

    session.add(
        ModelRun(
            organization_id=organization_id,
            kind=ModelKind.MATCHING,
            label=f"match:{getattr(matcher.embedder, 'name', 'unknown')}",
            params={
                "auto_accept": matcher.settings.match_auto_accept,
                "review_floor": matcher.settings.match_review_floor,
                "ambiguity_margin": matcher.settings.match_ambiguity_margin,
                "vector_weight": matcher.settings.match_vector_weight,
                "top_k": matcher.settings.match_top_k,
                "only_unmatched": only_unmatched,
            },
            metrics=report.as_dict(),
            n_products=len(listings),
        )
    )
    session.flush()
    logger.info("Matching run complete: %s", report.as_dict())
    return report
