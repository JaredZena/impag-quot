"""
Storefront integration routes (todoparaelcampo-storefront).

- GET  /storefront/products        machine-to-machine product feed for the
                                   storefront price sync GitHub Action,
                                   authenticated via X-API-Key (no Google auth)
- POST /storefront/publish         trigger the storefront publish-prices
                                   workflow via repository_dispatch
- GET  /storefront/publish-status  last run of the publish-prices workflow
"""

import os
import secrets

import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from auth import verify_google_token
from models import Product, get_db
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
    expected_key = os.getenv("STOREFRONT_API_KEY")
    if not expected_key:
        raise HTTPException(status_code=503, detail="STOREFRONT_API_KEY not configured")
    # Compare as bytes: str compare_digest raises TypeError on non-ASCII input,
    # which would turn a garbage header into a 500 instead of a 401.
    if not x_api_key or not secrets.compare_digest(
        x_api_key.encode("utf-8", "replace"), expected_key.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

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
                "is_active": p.is_active,
                "archived_at": p.archived_at,
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
