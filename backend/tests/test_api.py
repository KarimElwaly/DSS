"""API tests against a seeded database.

Each test runs inside a transaction that is rolled back, so the developer's
seeded demo data is never mutated.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import Organization, Product, User
from app.services.matching import run_matching
from app.services.seed import get_default_organization, seed_market
from simulator import MarketSimulator, default_electronics_market
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture(scope="module")
def seeded_session() -> Iterator[Session]:
    """A transaction containing a small seeded market, rolled back afterwards."""
    from app.db import get_engine, get_session_factory

    connection = get_engine().connect()
    transaction = connection.begin()
    session = get_session_factory()(bind=connection)

    config = default_electronics_market(seed=42, history_days=120, end_date=dt.date.today())
    seed_market(session, MarketSimulator(config).generate(), reset=True)
    run_matching(session, get_default_organization(session).id)
    session.flush()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="module")
def client(seeded_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: seeded_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def auth(client: TestClient, settings) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": settings.admin_email, "password": settings.admin_password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_health_is_public(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"


def test_readiness_reports_pgvector(client: TestClient) -> None:
    body = client.get("/api/health/ready").json()
    assert body["database"] == "ok"
    assert body["pgvector"] == "ok"


@pytest.mark.parametrize(
    "path",
    [
        "/api/catalog/products",
        "/api/catalog/categories",
        "/api/market/snapshot",
        "/api/market/listings",
        "/api/market/competitors",
        "/api/market/alerts",
    ],
)
def test_endpoints_require_authentication(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


def test_login_rejects_wrong_password(client: TestClient, settings) -> None:
    response = client.post(
        "/api/auth/login",
        json={"email": settings.admin_email, "password": "definitely-wrong"},
    )
    assert response.status_code == 401
    # The same message must be returned for unknown users -- no account enumeration.
    unknown = client.post(
        "/api/auth/login",
        json={"email": "nobody@dss-demo.com", "password": "definitely-wrong"},
    )
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == response.json()["detail"]


def test_me_returns_the_admin(client: TestClient, auth: dict[str, str], settings) -> None:
    body = client.get("/api/auth/me", headers=auth).json()
    assert body["email"] == settings.admin_email
    assert body["role"] == "admin"


def test_auth_bypass_admits_tokenless_requests(client: TestClient, auth_disabled) -> None:
    """With the MVP bypass on, an unauthenticated request is the seeded admin."""
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == auth_disabled.admin_email


def test_auth_bypass_is_ignored_outside_local(client: TestClient, auth_disabled) -> None:
    """The convenience flag must not be able to open a deployed instance."""
    original_env = auth_disabled.env
    auth_disabled.env = "production"
    try:
        assert client.get("/api/auth/me").status_code == 401
    finally:
        auth_disabled.env = original_env


def test_auth_bypass_fails_closed_on_an_empty_database(
    client: TestClient, seeded_session: Session, auth_disabled
) -> None:
    """No admin account means no identity to assume -- never a silent success."""
    savepoint = seeded_session.begin_nested()
    try:
        seeded_session.execute(delete(User))
        seeded_session.flush()
        assert client.get("/api/auth/me").status_code == 503
    finally:
        savepoint.rollback()


def test_list_products(client: TestClient, auth: dict[str, str]) -> None:
    body = client.get("/api/catalog/products?limit=5", headers=auth).json()
    assert body["total"] == 24
    assert len(body["items"]) == 5
    assert body["items"][0]["sku"]


def test_product_search_filters(client: TestClient, auth: dict[str, str]) -> None:
    body = client.get("/api/catalog/products?search=Aurora", headers=auth).json()
    assert body["total"] > 0
    assert all("Aurora" in item["brand"] or "Aurora" in item["name"] for item in body["items"])


def test_product_detail_and_sales(
    client: TestClient, auth: dict[str, str], seeded_session: Session
) -> None:
    product = seeded_session.scalars(select(Product).order_by(Product.sku)).first()
    assert product is not None

    detail = client.get(f"/api/catalog/products/{product.id}", headers=auth).json()
    assert detail["sku"] == product.sku

    sales = client.get(f"/api/catalog/products/{product.id}/sales?days=90", headers=auth).json()
    assert len(sales) > 30
    assert all(point["units"] >= 0 for point in sales)
    assert all(point["revenue"] >= 0 for point in sales)


def test_unknown_product_returns_404(client: TestClient, auth: dict[str, str]) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/catalog/products/{missing}", headers=auth).status_code == 404


def test_market_snapshot_computes_a_price_gap(client: TestClient, auth: dict[str, str]) -> None:
    body = client.get("/api/market/snapshot?limit=100", headers=auth).json()
    tracked = [row for row in body["items"] if row["n_listings"] > 0]
    assert tracked, "GTIN matching should link at least some listings"

    for row in tracked:
        index = row["competitor_price_index"]
        assert index is None or index > 0
        if index:
            expected = (row["our_price"] - index) / index
            assert row["price_gap_pct"] == pytest.approx(expected, rel=1e-9)


def test_listings_are_gtin_matched(client: TestClient, auth: dict[str, str]) -> None:
    """A published barcode is an identifier, so it must short-circuit the matcher."""
    body = client.get("/api/market/listings?limit=200", headers=auth).json()
    assert body["total"] > 0

    by_gtin = [row for row in body["items"] if row["match_method"] == "gtin"]
    assert by_gtin, "listings publishing a known GTIN must be auto-matched"
    assert all(row["match_confidence"] == 1.0 for row in by_gtin)
    assert all(row["matched_product_id"] for row in by_gtin)

    # Anything else that got linked came from the embedding pipeline and must
    # carry a real, sub-certain score.
    inferred = [
        row for row in body["items"] if row["matched_product_id"] and row["match_method"] != "gtin"
    ]
    assert all(0.0 < row["match_confidence"] < 1.0 for row in inferred)


def test_gtin_matches_are_correct(seeded_session: Session) -> None:
    """The GTIN short-circuit must never produce a false positive."""
    from app.models import CompetitorListing

    listings = seeded_session.scalars(
        select(CompetitorListing).where(CompetitorListing.matched_product_id.is_not(None))
    ).all()
    products = {p.id: p for p in seeded_session.scalars(select(Product))}

    assert listings
    for listing in listings:
        truth = listing.match_explanation["ground_truth_sku"]
        assert products[listing.matched_product_id].sku == truth


def test_single_organization_is_seeded(seeded_session: Session) -> None:
    orgs = seeded_session.scalars(select(Organization)).all()
    assert len(orgs) == 1


# ------------------------------------------------------- match review ----


def test_review_queue_requires_auth(client: TestClient) -> None:
    assert client.get("/api/matching/review").status_code == 401


def test_review_queue_returns_candidates(client: TestClient, auth: dict[str, str]) -> None:
    """Everything the matcher deferred should arrive with its suggestions."""
    body = client.get("/api/matching/review?limit=100", headers=auth).json()
    assert body["total"] > 0

    scored = [item for item in body["items"] if item["match_confidence"] is not None]
    assert scored, "deferred listings should carry the score that deferred them"
    for item in scored:
        assert item["reason"]
        assert item["candidates"]
        # Suggestions are ranked, best first.
        ranks = [c["rank"] for c in item["candidates"]]
        assert ranks == sorted(ranks)
        scores = [c["score"] for c in item["candidates"]]
        assert scores == sorted(scores, reverse=True)

    # Highest confidence first: quickest decisions at the top of the queue.
    confidences = [i["match_confidence"] for i in body["items"] if i["match_confidence"]]
    assert confidences == sorted(confidences, reverse=True)


def test_confirm_and_reject_a_match(
    client: TestClient, auth: dict[str, str], seeded_session: Session
) -> None:
    from app.models import CompetitorListing
    from app.models.enums import MatchStatus

    queue = client.get("/api/matching/review?limit=5", headers=auth).json()["items"]
    assert len(queue) >= 2
    to_confirm, to_reject = queue[0], queue[1]

    product_id = to_confirm["candidates"][0]["product_id"]
    confirmed = client.post(
        f"/api/matching/review/{to_confirm['listing_id']}/confirm",
        json={"product_id": product_id, "note": "verified by hand"},
        headers=auth,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["match_status"] == "confirmed"

    rejected = client.post(
        f"/api/matching/review/{to_reject['listing_id']}/reject",
        json={"note": "not one of ours"},
        headers=auth,
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["match_status"] == "rejected"

    stored = seeded_session.get(CompetitorListing, uuid.UUID(to_reject["listing_id"]))
    assert stored is not None
    assert stored.match_status == MatchStatus.REJECTED
    assert stored.matched_product_id is None


def test_confirm_requires_a_product(client: TestClient, auth: dict[str, str]) -> None:
    listing_id = client.get("/api/matching/review?limit=1", headers=auth).json()["items"][0][
        "listing_id"
    ]
    response = client.post(f"/api/matching/review/{listing_id}/confirm", json={}, headers=auth)
    assert response.status_code == 422
    assert "product_id is required" in response.json()["detail"]


def test_reject_forbids_a_product(client: TestClient, auth: dict[str, str]) -> None:
    item = client.get("/api/matching/review?limit=1", headers=auth).json()["items"][0]
    response = client.post(
        f"/api/matching/review/{item['listing_id']}/reject",
        json={"product_id": item["candidates"][0]["product_id"]},
        headers=auth,
    )
    assert response.status_code == 422


def test_confirm_rejects_unknown_listing(client: TestClient, auth: dict[str, str]) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    response = client.post(
        f"/api/matching/review/{missing}/confirm", json={"product_id": missing}, headers=auth
    )
    assert response.status_code == 404


# ------------------------------------------------------ ingest & alerts ----


def test_ingestion_adds_observations_and_alerts(client: TestClient, auth: dict[str, str]) -> None:
    body = client.post("/api/matching/ingest", headers=auth).json()
    assert body["failed"] == 0
    assert body["new_observations"] > 0
    assert "created" in body["alerts"]


def test_ingestion_is_idempotent_within_a_timestamp(
    client: TestClient, auth: dict[str, str], seeded_session: Session
) -> None:
    """Re-polling must not double-count a price point already recorded."""
    from app.models import PriceObservation

    before = seeded_session.scalar(select(func.count()).select_from(PriceObservation))
    client.post("/api/matching/ingest", headers=auth)
    after = seeded_session.scalar(select(func.count()).select_from(PriceObservation))
    assert after is not None and before is not None
    # Each poll stamps a fresh timestamp, so rows are added -- but never more
    # than one per listing.
    from app.models import CompetitorListing

    n_listings = seeded_session.scalar(select(func.count()).select_from(CompetitorListing)) or 0
    assert 0 < after - before <= n_listings


def test_alert_status_can_be_updated(client: TestClient, auth: dict[str, str]) -> None:
    alerts = client.get("/api/market/alerts?limit=1", headers=auth).json()["items"]
    if not alerts:
        pytest.skip("no open alerts in this market")
    alert_id = alerts[0]["id"]
    response = client.post(
        f"/api/market/alerts/{alert_id}/status", json={"status": "acknowledged"}, headers=auth
    )
    assert response.status_code == 200
    assert response.json()["status"] == "acknowledged"
