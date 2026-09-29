"""Manual API smoke test.

Exercises the walking-skeleton endpoints against a running server:
    python scripts/smoke_api.py [base_url]
"""

from __future__ import annotations

import json
import sys

import httpx

from app.core.config import get_settings


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
    settings = get_settings()

    with httpx.Client(base_url=base, timeout=30.0) as client:
        health = client.get("/api/health").raise_for_status().json()
        print("health           ", health)
        print("ready            ", client.get("/api/health/ready").json())

        unauth = client.get("/api/catalog/products")
        print("unauthenticated  ", unauth.status_code, "(expected 401)")
        assert unauth.status_code == 401, "catalog must require authentication"

        token = (
            client.post(
                "/api/auth/login",
                json={"email": settings.admin_email, "password": settings.admin_password},
            )
            .raise_for_status()
            .json()["access_token"]
        )
        client.headers["Authorization"] = f"Bearer {token}"
        print("login            ok")

        me = client.get("/api/auth/me").raise_for_status().json()
        print("me               ", me["email"], me["role"])

        bad = client.post(
            "/api/auth/login", json={"email": settings.admin_email, "password": "wrongpassword"}
        )
        print("bad password     ", bad.status_code, "(expected 401)")
        assert bad.status_code == 401

        products = (
            client.get("/api/catalog/products", params={"limit": 3}).raise_for_status().json()
        )
        print(
            "products         ", products["total"], "total;", [p["sku"] for p in products["items"]]
        )

        product_id = products["items"][0]["id"]
        detail = client.get(f"/api/catalog/products/{product_id}").raise_for_status().json()
        print("product detail   ", detail["sku"], "market:", json.dumps(detail["market"]))

        sales = client.get(f"/api/catalog/products/{product_id}/sales", params={"days": 30})
        print("sales points     ", len(sales.raise_for_status().json()))

        snapshot = client.get("/api/market/snapshot", params={"limit": 3}).raise_for_status().json()
        first = snapshot["items"][0]
        print(
            "market snapshot  ",
            first["sku"],
            f"ours={first['our_price']}",
            f"index={first['competitor_price_index']}",
            f"gap={first['price_gap_pct']}",
            f"listings={first['n_listings']}",
        )

        listings = client.get("/api/market/listings", params={"limit": 2}).raise_for_status().json()
        print("listings         ", listings["total"], "total")

        competitors = client.get("/api/market/competitors").raise_for_status().json()
        print("competitors      ", [c["slug"] for c in competitors])

        alerts = client.get("/api/market/alerts").raise_for_status().json()
        print("alerts           ", alerts["total"])

    print("\nAll smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
