"""Tests for the Module A matching pipeline and connectors.

The accuracy assertions are deliberately stated as *properties* rather than
exact figures: a threshold tweak should not break the suite, but a regression
that starts linking competitor products we do not sell must.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select

from app.models.catalog import EMBEDDING_DIM, Product
from app.models.enums import MatchMethod, MatchStatus, StockState
from app.models.market import CompetitorListing
from app.services.connectors.base import ConnectorError
from app.services.connectors.csv_feed import CsvConnector
from app.services.connectors.http_feed import assert_fetchable, parse_price, parse_stock
from app.services.matching import TfidfSvdEmbedder, run_matching
from app.services.matching.text import normalize
from app.services.seed import get_default_organization, seed_market
from simulator import MarketSimulator, default_electronics_market
from tests.conftest import requires_db

# ---------------------------------------------------------------- text ----


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Aurora A1 128GB", "aurora a1 128 gb"),
        ("AURORA  A1   128gb", "aurora a1 128 gb"),
        ("Aurora A1 128 GB - Unlocked", "aurora a1 128 gb unlocked"),
        ("Orbit 3 Sport 46mm", "orbit 3 sport 46 mm"),
        ("Café Latte™", "cafe latte"),
        ("", ""),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_normalize_makes_attached_units_comparable() -> None:
    """The capacity is often the only token separating two variants."""
    assert normalize("Nexon N5 128GB") == normalize("nexon  n5  128 gb")
    assert normalize("Nexon N5 128GB") != normalize("Nexon N5 256GB")


# ------------------------------------------------------------ embedder ----


@pytest.fixture(scope="module")
def corpus() -> list[str]:
    return [
        "Aurora A1 128GB smartphone",
        "Aurora A1 256GB smartphone",
        "Nexon N5 128GB smartphone",
        "Volta Book 14 512GB laptop",
        "Volta Book 16 Studio 1TB laptop",
        "Pulse 70 ANC Over-Ear Black headphones",
        "Echo 20 True Wireless Buds",
        "Orbit 3 42mm smartwatch",
    ]


def test_embedder_emits_fixed_width_unit_vectors(corpus: list[str]) -> None:
    """The column is vector(384) regardless of how small the corpus is."""
    vectors = TfidfSvdEmbedder().fit(corpus).encode(corpus)
    assert vectors.shape == (len(corpus), EMBEDDING_DIM)
    norms = np.linalg.norm(vectors, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_zero_padding_preserves_cosine_similarity(corpus: list[str]) -> None:
    """Padding is only safe because it leaves every pairwise angle unchanged."""
    embedder = TfidfSvdEmbedder().fit(corpus)
    padded = embedder.encode(corpus)
    used = embedder._n_components
    truncated = padded[:, :used]

    full = padded @ padded.T
    partial = truncated @ truncated.T
    assert np.allclose(full, partial, atol=1e-6)


def test_embedder_places_variants_closer_than_categories(corpus: list[str]) -> None:
    embedder = TfidfSvdEmbedder().fit(corpus)
    vectors = embedder.encode(
        ["Aurora A1 128GB smartphone", "Aurora A1 256GB smartphone", "Orbit 3 42mm smartwatch"]
    )
    sibling = float(vectors[0] @ vectors[1])
    unrelated = float(vectors[0] @ vectors[2])
    assert sibling > unrelated


def test_embedder_requires_fitting() -> None:
    with pytest.raises(RuntimeError, match="not fitted"):
        TfidfSvdEmbedder().encode(["anything"])


def test_embedder_round_trips_through_disk(corpus: list[str], tmp_path: Path) -> None:
    embedder = TfidfSvdEmbedder().fit(corpus)
    path = embedder.save(tmp_path / "embedder.joblib")
    restored = TfidfSvdEmbedder.load(path)
    assert np.allclose(embedder.encode(corpus), restored.encode(corpus))


# ------------------------------------------------------- http guardrails ----


def test_allowlist_blocks_unlisted_hosts() -> None:
    with pytest.raises(ConnectorError, match="not in DSS_HTTP_CONNECTOR_ALLOWLIST"):
        assert_fetchable("https://evil.example/p/1", ("shop.example",))


def test_empty_allowlist_disables_the_connector() -> None:
    """Closed by default: an unset allowlist must not mean 'fetch anything'."""
    with pytest.raises(ConnectorError, match="disabled"):
        assert_fetchable("https://shop.example/p/1", ())


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://shop.example/x", "gopher://x/1"])
def test_only_http_schemes_are_allowed(url: str) -> None:
    with pytest.raises(ConnectorError, match="scheme"):
        assert_fetchable(url, ("shop.example",))


def test_allowlisted_name_resolving_to_localhost_is_refused() -> None:
    """Guards against an allowlisted name pointing at an internal service."""
    with pytest.raises(ConnectorError, match="non-public address"):
        assert_fetchable("http://localhost:8010/admin", ("localhost",))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$1,299.00", Decimal("1299.00")),
        ("1299", Decimal("1299")),
        ("€ 49,99", Decimal("49.99")),
        ("Price: 19.99 USD", Decimal("19.99")),
        ("", None),
        ("out of stock", None),
        ("0", None),
    ],
)
def test_parse_price(text: str, expected: Decimal | None) -> None:
    assert parse_price(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("In Stock", StockState.IN_STOCK),
        ("Currently out of stock", StockState.OUT_OF_STOCK),
        ("Sold Out", StockState.OUT_OF_STOCK),
        ("???", StockState.UNKNOWN),
    ],
)
def test_parse_stock(text: str, expected: StockState) -> None:
    assert parse_stock(text) == expected


# ------------------------------------------------------- csv connector ----


def _competitor_stub(tmp_path: Path, filename: str):
    from app.models.market import Competitor

    return Competitor(
        name="Test Shop",
        slug="test-shop",
        domain="test.example",
        connector_config={"path": filename},
    )


def test_csv_connector_maps_aliased_columns(tmp_path: Path) -> None:
    (tmp_path / "feed.csv").write_text(
        "SKU,Product Name,Manufacturer,EAN,Current Price,Availability\n"
        "A-1,Aurora A1 128GB,Aurora,0750112000015,649.00,In Stock\n"
        "A-2,Aurora A1 256GB,Aurora,,729.00,Out of stock\n",
        encoding="utf-8",
    )
    result = CsvConnector(base_dir=tmp_path).fetch(
        _competitor_stub(tmp_path, "feed.csv"), now=dt.datetime.now(dt.UTC)
    )

    assert len(result.listings) == 2
    assert result.listings[0].brand == "Aurora"
    assert result.listings[0].gtin == "0750112000015"
    assert result.listings[1].gtin is None
    assert result.observations[0].price == Decimal("649.00")
    assert result.observations[0].stock_state == StockState.IN_STOCK
    assert result.observations[1].stock_state == StockState.OUT_OF_STOCK


def test_csv_connector_skips_bad_rows_without_failing(tmp_path: Path) -> None:
    """One malformed line must not cost us the whole feed."""
    (tmp_path / "feed.csv").write_text(
        "sku,price\nA-1,649.00\n,500.00\nA-3,not-a-number\nA-4,-5\n",
        encoding="utf-8",
    )
    result = CsvConnector(base_dir=tmp_path).fetch(
        _competitor_stub(tmp_path, "feed.csv"), now=dt.datetime.now(dt.UTC)
    )
    assert len(result.observations) == 1
    assert len(result.errors) == 3


def test_csv_connector_refuses_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ConnectorError, match="outside the upload directory"):
        CsvConnector(base_dir=tmp_path / "uploads").fetch(
            _competitor_stub(tmp_path, "../../../etc/passwd"), now=dt.datetime.now(dt.UTC)
        )


def test_csv_connector_reports_missing_columns(tmp_path: Path) -> None:
    (tmp_path / "feed.csv").write_text("colour,size\nred,large\n", encoding="utf-8")
    with pytest.raises(ConnectorError, match="missing required column"):
        CsvConnector(base_dir=tmp_path).fetch(
            _competitor_stub(tmp_path, "feed.csv"), now=dt.datetime.now(dt.UTC)
        )


# ---------------------------------------------------- matching pipeline ----


@pytest.fixture(scope="module")
def matched_db():
    """A small seeded market with the matcher already run over it."""
    from app.db import get_engine, get_session_factory

    connection = get_engine().connect()
    transaction = connection.begin()
    session = get_session_factory()(bind=connection)

    config = default_electronics_market(seed=99, history_days=90, end_date=dt.date.today())
    seed_market(session, MarketSimulator(config).generate(), reset=True)
    run_matching(session, get_default_organization(session).id)
    session.flush()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@requires_db
def test_matcher_produces_no_false_positives(matched_db) -> None:
    """The headline property: never link a listing to the wrong product.

    A missed match leaves a gap in the competitor price index. A wrong one
    corrupts it, and every elasticity estimate and price recommendation built
    on that SKU inherits the error.
    """
    skus = dict(matched_db.execute(select(Product.id, Product.sku)).all())
    accepted = matched_db.scalars(
        select(CompetitorListing).where(
            CompetitorListing.match_status.in_([MatchStatus.AUTO_ACCEPTED, MatchStatus.CONFIRMED]),
            CompetitorListing.matched_product_id.is_not(None),
        )
    ).all()

    assert accepted, "the matcher must link something"
    wrong = [
        (row.title, row.match_explanation.get("ground_truth_sku"), skus[row.matched_product_id])
        for row in accepted
        if skus[row.matched_product_id] != row.match_explanation.get("ground_truth_sku")
    ]
    assert not wrong, f"false positives: {wrong}"


@requires_db
def test_distractors_are_never_auto_accepted(matched_db) -> None:
    """Listings for products we do not sell must stay out of the price index."""
    # Filtered in Python: `match_explanation['ground_truth_sku']` yields a JSON
    # null, which is not SQL NULL, so `.is_(None)` would never match.
    distractors = [
        row
        for row in matched_db.scalars(select(CompetitorListing))
        if row.match_explanation.get("ground_truth_sku") is None
    ]
    assert distractors, "the simulator must generate hard negatives"
    assert all(row.matched_product_id is None for row in distractors)


@requires_db
def test_gtin_listings_are_matched_exactly(matched_db) -> None:
    gtin_matched = matched_db.scalars(
        select(CompetitorListing).where(CompetitorListing.match_method == MatchMethod.GTIN)
    ).all()
    assert gtin_matched
    assert all(row.match_confidence == 1.0 for row in gtin_matched)
    assert all(row.match_status == MatchStatus.AUTO_ACCEPTED for row in gtin_matched)


@requires_db
def test_every_listing_gets_a_decision(matched_db) -> None:
    undecided = matched_db.scalars(
        select(CompetitorListing).where(CompetitorListing.match_explanation == {})
    ).all()
    assert not undecided


@requires_db
def test_rerun_preserves_human_decisions(matched_db) -> None:
    """A person's ruling outranks the model, permanently."""
    org = get_default_organization(matched_db)
    listing = matched_db.scalars(
        select(CompetitorListing).where(CompetitorListing.match_status == MatchStatus.PENDING)
    ).first()
    assert listing is not None

    product = matched_db.scalars(select(Product).order_by(Product.sku)).first()
    assert product is not None
    listing.matched_product_id = product.id
    listing.match_status = MatchStatus.CONFIRMED
    listing.match_method = MatchMethod.MANUAL
    matched_db.flush()

    report = run_matching(matched_db, org.id)
    matched_db.refresh(listing)

    assert report.skipped_human_decided >= 1
    assert listing.match_status == MatchStatus.CONFIRMED
    assert listing.matched_product_id == product.id


@requires_db
def test_catalog_is_embedded(matched_db) -> None:
    products = matched_db.scalars(select(Product)).all()
    assert products
    assert all(p.embedding is not None for p in products)
    assert all(len(p.embedding) == EMBEDDING_DIM for p in products)
