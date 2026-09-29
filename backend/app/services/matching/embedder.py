"""Sentence embeddings for competitor-listing matching.

Two implementations sit behind one protocol:

``TfidfSvdEmbedder`` (default)
    TF-IDF over word *and* character n-grams, reduced to a dense vector with
    truncated SVD (latent semantic analysis). Pure scikit-learn, trains in
    under a second on a laptop, and adds no wheel larger than a few megabytes.

``SentenceTransformerEmbedder`` (optional ``transformers`` extra)
    A real MiniLM encoder. Better on paraphrase-style titles, but it drags in
    ~2.5 GB of PyTorch, so it is opt-in rather than a hard dependency.

Both emit L2-normalised vectors of exactly :data:`EMBEDDING_DIM` components, so
swapping one for the other needs no schema change and no code change outside
:func:`build_embedder`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import joblib
import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import Normalizer

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.market import EMBEDDING_DIM
from app.services.matching.text import normalize

logger = get_logger(__name__)


@runtime_checkable
class Embedder(Protocol):
    """Anything that can turn product/listing text into comparable vectors."""

    name: str
    dim: int

    def fit(self, corpus: Sequence[str]) -> Embedder: ...

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


def _pad(matrix: np.ndarray, dim: int) -> np.ndarray:
    """Right-pad with zeros to a fixed width.

    Padding an L2-normalised vector with zeros leaves its norm at 1 and leaves
    every pairwise cosine similarity untouched, so this is lossless. It lets a
    small corpus (which cannot support 384 latent components) share the same
    ``vector(384)`` column as a large one.
    """
    if matrix.shape[1] == dim:
        return matrix
    if matrix.shape[1] > dim:
        raise ValueError(f"Embedding width {matrix.shape[1]} exceeds the column width {dim}.")
    out = np.zeros((matrix.shape[0], dim), dtype=np.float32)
    out[:, : matrix.shape[1]] = matrix
    return out


class TfidfSvdEmbedder:
    """Latent semantic analysis over word and character n-grams.

    The character n-grams are what make this workable on scraped titles: they
    survive dropped letters, missing spaces and inconsistent casing, none of
    which a word-only model tolerates.
    """

    name = "tfidf-svd"

    def __init__(self, dim: int = EMBEDDING_DIM, random_state: int = 42) -> None:
        self.dim = dim
        self.random_state = random_state
        self._pipeline: Pipeline | None = None
        self._n_components = 0

    @property
    def is_fitted(self) -> bool:
        return self._pipeline is not None

    def fit(self, corpus: Sequence[str]) -> TfidfSvdEmbedder:
        documents = [normalize(text) for text in corpus]
        documents = [doc for doc in documents if doc]
        if len(documents) < 2:
            raise ValueError("At least two non-empty documents are required to fit the embedder.")

        features = FeatureUnion(
            [
                ("word", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), sublinear_tf=True)),
                (
                    "char",
                    TfidfVectorizer(
                        analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True
                    ),
                ),
            ]
        )
        matrix = features.fit_transform(documents)

        # Truncated SVD cannot produce more components than the smaller of the
        # two matrix dimensions; a demo catalog has far fewer documents than the
        # 384 the column allows, so we take what the data supports and pad.
        self._n_components = max(1, min(self.dim, matrix.shape[0] - 1, matrix.shape[1] - 1))
        svd = TruncatedSVD(n_components=self._n_components, random_state=self.random_state)
        self._pipeline = Pipeline(
            [("features", features), ("svd", svd), ("normalize", Normalizer(copy=False))]
        )
        self._pipeline.fit(documents)

        explained = float(svd.explained_variance_ratio_.sum())
        logger.info(
            "Fitted %s on %d documents: %d components, %.1f%% variance explained",
            self.name,
            len(documents),
            self._n_components,
            explained * 100,
        )
        return self

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if self._pipeline is None:
            raise RuntimeError("Embedder is not fitted; call fit() or load() first.")
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        reduced = self._pipeline.transform([normalize(text) for text in texts])
        return _pad(np.asarray(reduced, dtype=np.float32), self.dim)

    @property
    def metadata(self) -> dict[str, Any]:
        return {"embedder": self.name, "dim": self.dim, "n_components": self._n_components}

    def save(self, path: Path) -> Path:
        if self._pipeline is None:
            raise RuntimeError("Refusing to save an unfitted embedder.")
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"pipeline": self._pipeline, "meta": self.metadata}, path)
        path.with_suffix(".json").write_text(json.dumps(self.metadata, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> TfidfSvdEmbedder:
        payload = joblib.load(path)
        embedder = cls(dim=int(payload["meta"]["dim"]))
        embedder._pipeline = payload["pipeline"]
        embedder._n_components = int(payload["meta"]["n_components"])
        return embedder


class SentenceTransformerEmbedder:
    """MiniLM encoder. Requires the optional ``transformers`` extra."""

    name = "sentence-transformer"

    def __init__(self, model_name: str, dim: int = EMBEDDING_DIM) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "sentence-transformers is not installed. "
                'Install the optional extra: pip install -e "backend[transformers]"'
            ) from exc
        self.dim = dim
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)

    def fit(self, corpus: Sequence[str]) -> SentenceTransformerEmbedder:
        """No-op: the encoder is pre-trained."""
        return self

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vectors = self._model.encode(
            [normalize(text) for text in texts],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return _pad(np.asarray(vectors, dtype=np.float32), self.dim)

    @property
    def metadata(self) -> dict[str, Any]:
        return {"embedder": self.name, "dim": self.dim, "model": self.model_name}


def artifact_path(name: str = "tfidf-svd") -> Path:
    return get_settings().artifact_dir / "matching" / f"{name}.joblib"


def build_embedder(kind: str | None = None) -> Embedder:
    """Construct the configured embedder. ``kind`` overrides the setting."""
    settings = get_settings()
    chosen = (kind or settings.embedder).strip().lower()
    if chosen in {"tfidf", "tfidf-svd", "lsa"}:
        return TfidfSvdEmbedder(dim=settings.embedding_dim)
    if chosen in {"minilm", "sentence-transformer", "st"}:
        return SentenceTransformerEmbedder(settings.embedding_model, dim=settings.embedding_dim)
    raise ValueError(f"Unknown embedder '{chosen}'. Use 'tfidf-svd' or 'sentence-transformer'.")
