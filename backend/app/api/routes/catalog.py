"""Catalog browsing endpoints."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentOrg, CurrentUser, DbSession, Pagination
from app.models.catalog import Category, Product, SalesFact
from app.models.enums import ProductStatus
from app.schemas.catalog import (
    CategoryOut,
    ProductDetail,
    ProductMarketPosition,
    ProductOut,
    SalesPoint,
)
from app.schemas.common import Page
from app.services.market_view import load_market_positions

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(session: DbSession, org: CurrentOrg, _: CurrentUser) -> list[Category]:
    stmt = select(Category).where(Category.organization_id == org.id).order_by(Category.name)
    return list(session.scalars(stmt))


@router.get("/products", response_model=Page[ProductOut])
def list_products(
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
    page: Pagination,
    search: str | None = Query(default=None, min_length=1, max_length=120),
    category_id: uuid.UUID | None = None,
    product_status: ProductStatus | None = Query(default=None, alias="status"),
) -> Page[ProductOut]:
    filters = [Product.organization_id == org.id]
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(
            Product.name.ilike(pattern) | Product.sku.ilike(pattern) | Product.brand.ilike(pattern)
        )
    if category_id is not None:
        filters.append(Product.category_id == category_id)
    if product_status is not None:
        filters.append(Product.status == product_status)

    total = session.scalar(select(func.count()).select_from(Product).where(*filters)) or 0
    stmt = (
        select(Product)
        .where(*filters)
        .options(selectinload(Product.category))
        .order_by(Product.sku)
        .limit(page.limit)
        .offset(page.offset)
    )
    items = [ProductOut.model_validate(p) for p in session.scalars(stmt)]
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/products/{product_id}", response_model=ProductDetail)
def get_product(
    product_id: uuid.UUID, session: DbSession, org: CurrentOrg, _: CurrentUser
) -> ProductDetail:
    product = session.scalars(
        select(Product)
        .where(Product.id == product_id, Product.organization_id == org.id)
        .options(selectinload(Product.category))
    ).first()
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")

    detail = ProductDetail.model_validate(product)
    position = load_market_positions(session, org.id, [product.id]).get(product.id)
    if position is not None:
        cheapest = position.cheapest
        detail.market = ProductMarketPosition(
            cheapest_competitor=cheapest.competitor_name if cheapest else None,
            cheapest_competitor_price=cheapest.landed_price if cheapest else None,
            competitor_price_index=position.price_index,
            price_gap_pct=position.price_gap_pct(float(product.current_price)),
            n_listings=position.n_listings,
            n_out_of_stock=position.n_out_of_stock,
            observed_at=position.observed_at,
        )
    return detail


@router.get("/products/{product_id}/sales", response_model=list[SalesPoint])
def get_product_sales(
    product_id: uuid.UUID,
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
    days: int = Query(default=180, ge=7, le=1095),
) -> list[SalesPoint]:
    exists = session.scalar(
        select(func.count())
        .select_from(Product)
        .where(Product.id == product_id, Product.organization_id == org.id)
    )
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")

    cutoff = dt.date.today() - dt.timedelta(days=days)
    stmt = (
        select(SalesFact)
        .where(SalesFact.product_id == product_id, SalesFact.sale_date >= cutoff)
        .order_by(SalesFact.sale_date)
    )
    return [
        SalesPoint(
            sale_date=row.sale_date,
            units=row.units,
            unit_price=float(row.unit_price),
            unit_cost=float(row.unit_cost),
            promo_flag=row.promo_flag,
            revenue=float(row.revenue),
            gross_margin=float(row.gross_margin),
        )
        for row in session.scalars(stmt)
    ]
