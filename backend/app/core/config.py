"""Application settings.

All configuration is declared as ``Settings`` fields so that values are resolved
from the environment *and* the ``.env`` file at call time. Never read
``os.environ`` at module import time -- it silently bypasses ``.env`` loading.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DSS_",
        env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- core ---------------------------------------------------------------
    env: str = "local"
    log_level: str = "INFO"
    secret_key: str = "dev-secret-key-change-me-32-characters"
    api_prefix: str = "/api"

    # -- database -----------------------------------------------------------
    database_url: str = "postgresql+psycopg://dss:dss@localhost:55432/dss"
    db_echo: bool = False

    # -- auth ---------------------------------------------------------------
    admin_email: str = "admin@dss-demo.com"
    admin_password: str = "admin12345"
    access_token_ttl_minutes: int = 720

    #: Treat every request as the seeded admin, with no token required.
    #: A convenience for demoing and for front-end work; it is ignored unless
    #: ``env == "local"`` so that enabling it can never open a deployed
    #: instance, even if the variable is set there by mistake.
    auth_disabled: bool = False

    # -- matching -----------------------------------------------------------
    #: "tfidf-svd" (default, pure scikit-learn) or "sentence-transformer"
    #: (needs the optional `transformers` extra and a ~2.5 GB PyTorch install).
    embedder: str = "tfidf-svd"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384
    #: Score at or above which a match is applied without human review.
    #: Chosen by ``scripts/tune_match_threshold.py``: the lowest value that
    #: produced zero false positives pooled over five independently generated
    #: markets. A sweep on any single market suggests ~0.78, which does not
    #: generalise -- that is why the tuning script exists.
    match_auto_accept: float = 0.86
    match_review_floor: float = 0.60
    #: Weight of the vector score when blended with the fuzzy score.
    match_vector_weight: float = 0.6
    #: Nearest neighbours pulled from pgvector before re-ranking.
    match_top_k: int = 5
    #: A winner this close to the runner-up is treated as ambiguous and sent to
    #: review regardless of its absolute score. Catalogs full of near-identical
    #: variants (same model, different capacity) make confident-but-wrong the
    #: most expensive failure mode, because a bad match corrupts the price index.
    match_ambiguity_margin: float = 0.05

    # -- llm ----------------------------------------------------------------
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    llm_enabled: bool = True
    llm_timeout_seconds: float = 120.0

    # -- ingestion ----------------------------------------------------------
    scheduler_enabled: bool = False
    crawl_interval_minutes: int = 60
    #: Comma-separated hostnames the generic HTTP connector may fetch (SSRF guard).
    #: Kept as a raw string: pydantic-settings would otherwise JSON-decode a list
    #: field coming from ``.env`` and fail on the empty default.
    http_connector_allowlist: str = ""
    http_connector_timeout_seconds: float = 15.0

    # -- artifacts ----------------------------------------------------------
    artifact_dir: Path = BACKEND_ROOT / "artifacts"

    @property
    def allowed_connector_hosts(self) -> tuple[str, ...]:
        return tuple(
            item.strip().lower()
            for item in self.http_connector_allowlist.split(",")
            if item.strip()
        )

    @property
    def is_local(self) -> bool:
        return self.env == "local"

    @property
    def auth_bypass_active(self) -> bool:
        """True only when the bypass is requested *and* we are running locally."""
        return self.auth_disabled and self.is_local


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    return settings
