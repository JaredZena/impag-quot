"""
Storefront integration routes (todoparaelcampo-storefront).

- GET  /storefront/products        machine-to-machine product feed for the
                                   storefront price sync GitHub Action,
                                   authenticated via X-API-Key (no Google auth)
- POST /storefront/publish         trigger the storefront publish-prices
                                   workflow via repository_dispatch
- GET  /storefront/publish-status  last run of the publish-prices workflow
- GET  /storefront/visibility      hidden store pages for the sync (X-API-Key)
- GET  /storefront/pages/visibility  «Mostrar en la tienda» rows (admin)
- PUT  /storefront/pages/visibility  hide / show one store page (admin)
- GET  /storefront/links           admin-picked page -> product links for the
                                   sync (X-API-Key)
- GET  /storefront/pages/links     «Ligar producto» rows (admin)
- PUT  /storefront/pages/link      link / unlink one store page (admin)
- GET  /storefront/new-pages       admin-created pages for the sync (X-API-Key)
- GET  /storefront/pages/new       «Nueva página» rows (admin)
- POST /storefront/pages/new       create a store page from a product (admin)
- DELETE /storefront/pages/new/{handle}  take an admin-created page off (admin)
"""

import os
import re
import secrets
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from auth import verify_google_token
from models import (
    Product,
    StorefrontNewPage,
    StorefrontPageLink,
    StorefrontPageVisibility,
    get_db,
)
from services.price_calculator import (
    calculate_product_price_with_currency,
    get_lowest_supplier_cost_with_currency,
)

# NOTE: unlike routes/products.py, this router must NOT apply
# verify_google_token at router level — GET /products below is called by a
# GitHub Action authenticated with X-API-Key, not by a person.
router = APIRouter(prefix="/storefront", tags=["storefront"])

GITHUB_API_BASE = "https://api.github.com"
GITHUB_STOREFRONT_REPO = "JaredZena/todoparaelcampo-storefront"
GITHUB_TIMEOUT_SECONDS = 15


def _github_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _require_feed_key(x_api_key: str | None) -> None:
    """X-API-Key auth shared by the machine-to-machine feeds (the sync Action)."""
    expected_key = os.getenv("STOREFRONT_API_KEY")
    if not expected_key:
        raise HTTPException(status_code=503, detail="STOREFRONT_API_KEY not configured")
    # Compare as bytes: str compare_digest raises TypeError on non-ASCII input,
    # which would turn a garbage header into a 500 instead of a 401.
    if not x_api_key or not secrets.compare_digest(
        x_api_key.encode("utf-8", "replace"), expected_key.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@router.get("/products")
def get_storefront_products(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    is_active: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """Product feed for scripts/backend_sync.py in todoparaelcampo-storefront.

    Replaces the legacy public GET /products pull. Returns the same top-level
    shape as the legacy endpoint ({"success", "data", "error", "message"}) so
    the sync script's pagination (batch = payload["data"], stop when fewer
    than `limit` items return) keeps working unchanged.
    """
    _require_feed_key(x_api_key)

    # Public base URL for product images (e.g. an R2 custom domain / public
    # bucket domain). Read at REQUEST time so App Runner env changes apply
    # without a code deploy. NEVER emit presigned URLs here — they expire, and
    # the storefront bakes these URLs statically.
    r2_public_base = os.getenv("R2_PUBLIC_BASE_URL")

    products = (
        db.query(Product)
        .filter(Product.archived_at.is_(None))
        .filter(Product.is_active == is_active)
        .order_by(Product.id.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    data = []
    for p in products:
        # Currency of the effective price, mirroring legacy GET /products
        # (routes/products.py) via the shared price_calculator helpers.
        calculated_currency = None
        if p.price is None and p.calculated_price is not None:
            price_currency = calculate_product_price_with_currency(p, db)
            if price_currency:
                _, calculated_currency = price_currency
        elif p.price is not None:
            lowest_cost_currency = get_lowest_supplier_cost_with_currency(p.id, db)
            if lowest_cost_currency:
                _, calculated_currency = lowest_cost_currency
            else:
                calculated_currency = "MXN"

        data.append(
            {
                "id": p.id,
                "name": p.name,
                # Customer-facing title set by the team in the admin; the sync
                # applies it only when non-empty.
                "storefront_title": p.storefront_title,
                # "Especificaciones en la página": page rows, one per line; the
                # sync replaces the page's description only when non-empty.
                "storefront_description": p.storefront_description,
                # The product's own description: the first text of a page made
                # with «Nueva página» when storefront_description is empty.
                "description": p.description,
                "sku": p.sku,
                # Manual price if set, else the cached calculated fallback —
                # identical to legacy GET /products.
                "price": (
                    float(p.price)
                    if p.price is not None
                    else (
                        float(p.calculated_price)
                        if p.calculated_price is not None
                        else None
                    )
                ),
                "calculated_price": (
                    float(p.calculated_price)
                    if p.calculated_price is not None
                    else None
                ),
                "is_calculated_price": p.price is None
                and p.calculated_price is not None,
                "currency": calculated_currency,
                # Public image URLs in display order (first = primary). Empty
                # list when R2_PUBLIC_BASE_URL is unset (the sync skips empty).
                "images": (
                    [f"{r2_public_base.rstrip('/')}/{key}" for key in (p.images or [])]
                    if r2_public_base
                    else []
                ),
                "is_active": p.is_active,
                "archived_at": p.archived_at,
                # Checkout-gate inputs for the storefront sync: a product only
                # becomes buyable when iva agrees with its accountant-approved
                # rate and unit matches the expected unit. NULL stays null so
                # the sync fails closed.
                "iva": p.iva,
                "unit": p.unit.value if p.unit is not None else None,
                # "Vender en línea" switch set in the admin; the sync builds the
                # sale entry from it. NULL = not configured there, so the sync
                # falls back to its own config.
                "online_sale": p.online_sale,
                "package_size": p.package_size,
                "stock": p.stock,
            }
        )

    return {"success": True, "data": data, "error": None, "message": None}


@router.post("/publish")
def trigger_storefront_publish(user: dict = Depends(verify_google_token)):
    """Dispatch the publish-prices workflow in the storefront repo."""
    token = os.getenv("GITHUB_STOREFRONT_TOKEN")
    if not token:
        raise HTTPException(
            status_code=503, detail="GITHUB_STOREFRONT_TOKEN not configured"
        )

    try:
        resp = requests.post(
            f"{GITHUB_API_BASE}/repos/{GITHUB_STOREFRONT_REPO}/dispatches",
            json={"event_type": "publish-prices"},
            headers=_github_headers(token),
            timeout=GITHUB_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"GitHub dispatch failed: {e}")

    if resp.status_code != 204:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub dispatch failed with status {resp.status_code}",
        )
    return {"status": "dispatched"}


@router.get("/publish-status")
def get_storefront_publish_status(user: dict = Depends(verify_google_token)):
    """Latest run of the publish-prices workflow in the storefront repo."""
    token = os.getenv("GITHUB_STOREFRONT_TOKEN")
    if not token:
        return {"configured": False}

    no_runs = {
        "configured": True,
        "status": None,
        "conclusion": None,
        "created_at": None,
        "html_url": None,
    }

    try:
        resp = requests.get(
            f"{GITHUB_API_BASE}/repos/{GITHUB_STOREFRONT_REPO}"
            "/actions/workflows/publish-prices.yml/runs",
            params={"per_page": 1},
            headers=_github_headers(token),
            timeout=GITHUB_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"GitHub status check failed: {e}")

    if resp.status_code == 404:
        # Workflow file doesn't exist (yet) — configured, but nothing to report.
        return no_runs
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub status check failed with status {resp.status_code}",
        )

    try:
        payload = resp.json() or {}
    except ValueError:
        raise HTTPException(
            status_code=502, detail="GitHub status response was not valid JSON"
        )
    runs = payload.get("workflow_runs") or []
    if not runs:
        return no_runs

    run = runs[0]
    return {
        "configured": True,
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "created_at": run.get("created_at"),
        "html_url": run.get("html_url"),
    }


# ------------------------------------------------- «Mostrar en la tienda»

# A storefront handle as the store writes it: lowercase words joined by "-",
# with the few Shopify-era marks (®, ™, º) some old pages still carry. Never a
# path, whitespace or markup.
_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9\-®™º]*$")
NOTE_MAX = 300


class PageVisibilityIn(BaseModel):
    handle: str = Field(min_length=1, max_length=255)
    hidden: bool
    note: str | None = Field(default=None, max_length=NOTE_MAX)

    @field_validator("handle")
    @classmethod
    def _handle(cls, v: str) -> str:
        v = v.strip()
        if not _HANDLE_RE.match(v):
            raise ValueError("invalid storefront handle")
        return v

    @field_validator("note")
    @classmethod
    def _note(cls, v: str | None) -> str | None:
        v = " ".join((v or "").split())
        return v or None


def _visibility_out(row: StorefrontPageVisibility) -> dict:
    return {
        "handle": row.handle,
        "hidden": bool(row.hidden),
        "note": row.note,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/visibility")
def get_storefront_visibility(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
):
    """Hidden store pages, for scripts/backend_sync.py (writes
    src/data/visibility-admin.json; the build leaves those pages out)."""
    _require_feed_key(x_api_key)
    rows = (
        db.query(StorefrontPageVisibility.handle)
        .filter(StorefrontPageVisibility.hidden.is_(True))
        .order_by(StorefrontPageVisibility.handle.asc())
        .all()
    )
    return {"hidden": [r.handle for r in rows]}


@router.get("/pages/visibility")
def list_page_visibility(
    user: dict = Depends(verify_google_token), db: Session = Depends(get_db)
):
    """Every page the admin has switched (hidden or shown again)."""
    rows = (
        db.query(StorefrontPageVisibility)
        .order_by(StorefrontPageVisibility.handle.asc())
        .all()
    )
    return {"pages": [_visibility_out(r) for r in rows]}


@router.put("/pages/visibility")
def set_page_visibility(
    body: PageVisibilityIn,
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Hide or show one store page. Takes effect on the next publish
    (the admin's «Publicar» button or the daily run)."""
    row = db.get(StorefrontPageVisibility, body.handle)
    if row is None:
        row = StorefrontPageVisibility(handle=body.handle)
        db.add(row)
    row.hidden = body.hidden
    row.note = body.note
    row.updated_by = (user or {}).get("email")
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _visibility_out(row)


class PageLinkIn(BaseModel):
    handle: str = Field(min_length=1, max_length=255)
    # None = the page has no product in the system (removes a wrong match).
    product_id: int | None = Field(ge=1)

    @field_validator("handle")
    @classmethod
    def _handle(cls, v: str) -> str:
        v = v.strip()
        if not _HANDLE_RE.match(v):
            raise ValueError("invalid storefront handle")
        return v


def _link_out(row: StorefrontPageLink) -> dict:
    product = row.product
    return {
        "handle": row.handle,
        "product_id": row.product_id,
        "product_name": product.name if product is not None else None,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/links")
def get_storefront_links(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
):
    """Admin-picked links, for scripts/backend_sync.py: each one wins over
    scripts/product-mapping.json for its page (product_id null = no product)."""
    _require_feed_key(x_api_key)
    rows = db.query(StorefrontPageLink).order_by(StorefrontPageLink.handle.asc()).all()
    return {"links": [{"handle": r.handle, "product_id": r.product_id} for r in rows]}


@router.get("/pages/links")
def list_page_links(
    user: dict = Depends(verify_google_token), db: Session = Depends(get_db)
):
    """Every page the admin has linked (or unlinked) by hand."""
    rows = db.query(StorefrontPageLink).order_by(StorefrontPageLink.handle.asc()).all()
    return {"links": [_link_out(r) for r in rows]}


@router.put("/pages/link")
def set_page_link(
    body: PageLinkIn,
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Link one store page to the product that prices it, or say it has none.
    Takes effect on the next publish (the admin's «Publicar» button or the
    daily run). Only an active, unarchived product can price a page: the sync
    never reads the others."""
    if body.product_id is not None:
        product = db.get(Product, body.product_id)
        if product is None or product.archived_at is not None or not product.is_active:
            raise HTTPException(
                status_code=404, detail="Product not found or not active"
            )
    row = db.get(StorefrontPageLink, body.handle)
    if row is None:
        row = StorefrontPageLink(handle=body.handle)
        db.add(row)
    row.product_id = body.product_id
    row.updated_by = (user or {}).get("email")
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _link_out(row)


# ------------------------------------------------------------ «Nueva página»

# A new page's handle: plain lowercase ASCII words joined by "-" (the admin
# builds it from the store name). No ®/™: a mark in a new URL broke checkout
# links once (10-03).
_NEW_HANDLE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CATEGORY_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class NewPageIn(BaseModel):
    handle: str = Field(min_length=3, max_length=120)
    product_id: int = Field(ge=1)
    category_slug: str = Field(min_length=2, max_length=100)

    @field_validator("handle")
    @classmethod
    def _handle(cls, v: str) -> str:
        v = v.strip()
        if not _NEW_HANDLE_RE.match(v):
            raise ValueError("invalid storefront handle")
        return v

    @field_validator("category_slug")
    @classmethod
    def _category(cls, v: str) -> str:
        v = v.strip()
        if not _CATEGORY_SLUG_RE.match(v):
            raise ValueError("invalid category")
        return v


def _new_page_out(row: StorefrontNewPage) -> dict:
    product = row.product
    return {
        "handle": row.handle,
        "product_id": row.product_id,
        "product_name": product.name if product is not None else None,
        # What the page is called in the store («Nombre en la tienda» or the name).
        "title": (
            (product.storefront_title or product.name) if product is not None else None
        ),
        "category_slug": row.category_slug,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/new-pages")
def get_storefront_new_pages(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
):
    """Admin-created pages, for scripts/backend_sync.py: it adds each page
    from its product and removes admin-made pages that have no row."""
    _require_feed_key(x_api_key)
    rows = db.query(StorefrontNewPage).order_by(StorefrontNewPage.handle.asc()).all()
    return {
        "pages": [
            {
                "handle": r.handle,
                "product_id": r.product_id,
                "category_slug": r.category_slug,
            }
            for r in rows
        ]
    }


@router.get("/pages/new")
def list_new_pages(
    user: dict = Depends(verify_google_token), db: Session = Depends(get_db)
):
    """Every page created in the admin, published or still waiting."""
    rows = (
        db.query(StorefrontNewPage)
        .order_by(StorefrontNewPage.created_at.desc(), StorefrontNewPage.handle.asc())
        .all()
    )
    return {"pages": [_new_page_out(r) for r in rows]}


@router.post("/pages/new", status_code=201)
def create_new_page(
    body: NewPageIn,
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Create a store page from a product. The page appears on the next
    publish (the admin's «Publicar» button or the daily run). The product needs
    a photo: the store never shows a page without one."""
    product = db.get(Product, body.product_id)
    if product is None or product.archived_at is not None or not product.is_active:
        raise HTTPException(status_code=404, detail="Product not found or not active")
    if not product.images:
        raise HTTPException(status_code=422, detail="product_has_no_photo")
    if (
        db.query(StorefrontNewPage)
        .filter(StorefrontNewPage.product_id == body.product_id)
        .first()
        is not None
    ):
        raise HTTPException(status_code=409, detail="product_has_page")
    # A link or switch row means the page already exists on the store.
    if (
        db.get(StorefrontNewPage, body.handle) is not None
        or db.get(StorefrontPageLink, body.handle) is not None
        or db.get(StorefrontPageVisibility, body.handle) is not None
    ):
        raise HTTPException(status_code=409, detail="handle_taken")
    row = StorefrontNewPage(
        handle=body.handle,
        product_id=body.product_id,
        category_slug=body.category_slug,
        created_by=(user or {}).get("email"),
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _new_page_out(row)


@router.delete("/pages/new/{handle}")
def delete_new_page(
    handle: str,
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Cancel a page that has not been published yet, or take a published
    admin-made page off the store on the next publish."""
    row = db.get(StorefrontNewPage, handle)
    if row is None:
        raise HTTPException(status_code=404, detail="Page not found")
    # The page's switch and link go with it, so its address can be used again.
    for extra in (StorefrontPageVisibility, StorefrontPageLink):
        db.query(extra).filter(extra.handle == handle).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    # A body, not 204: the admin's apiRequest always reads JSON.
    return {"deleted": handle}
