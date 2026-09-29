"""Product matching (Module A, step 2)."""

from app.services.matching.embedder import (
    Embedder,
    SentenceTransformerEmbedder,
    TfidfSvdEmbedder,
    build_embedder,
)
from app.services.matching.matcher import (
    Candidate,
    ListingMatcher,
    MatchOutcome,
    MatchReport,
    run_matching,
)
from app.services.matching.text import normalize

__all__ = [
    "Candidate",
    "Embedder",
    "ListingMatcher",
    "MatchOutcome",
    "MatchReport",
    "SentenceTransformerEmbedder",
    "TfidfSvdEmbedder",
    "build_embedder",
    "normalize",
    "run_matching",
]
